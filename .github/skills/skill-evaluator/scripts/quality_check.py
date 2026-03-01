#!/usr/bin/env python3
"""スキルの静的品質チェック。

agentskills.io のベストプラクティスガイドラインに基づいて
スキルの品質を検査する。セキュリティリスクは別セクションで報告する。

使い方:
    python quality_check.py                        # .github/skills/ 以下を全チェック
    python quality_check.py --skill <skill-name>   # 特定スキルのみ
    python quality_check.py --path <dir>           # 任意ディレクトリのスキルをチェック
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tokenize


# ──────────────────────────────────────────────
# フロントマター解析
# ──────────────────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """YAML フロントマターと本文を分離してパースする。

    Returns:
        (frontmatter_dict, body_text)
        ネストされた metadata キーは dict として返す。
    """
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    raw = parts[1].strip()
    body = parts[2]

    fm: dict = {}
    current_parent: str | None = None
    nested: dict = {}

    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")

        if indent == 0:
            if nested and current_parent:
                fm[current_parent] = nested
            current_parent = key
            nested = {}
            if value:
                fm[key] = value
                current_parent = None
                nested = {}
        else:
            if value:
                nested[key] = value

    if nested and current_parent:
        fm[current_parent] = nested

    return fm, body


# ──────────────────────────────────────────────
# 品質チェックルール定義
# ──────────────────────────────────────────────

_RESERVED_WORDS = {"anthropic"}

_AMBIGUOUS_NAMES = {
    "helper", "utils", "tools", "documents", "data", "files",
    "misc", "common", "general", "utility", "support",
}

_FIRST_PERSON_PATTERNS = [
    r"お手伝いできます",
    r"お手伝いします",
    r"ご支援します",
    r"\bI can\b",
    r"\bYou can use this to\b",
    r"\bThis helps you\b",
]

_TRIGGER_PATTERNS = [
    r"場合",
    r"とき",
    r"[Ww]hen",
    r"発動",
    r"などで",
    r"Use when",
    r"リクエストで",
    r"で使用する",
]

_NETWORK_PATTERNS = [
    r"\brequests\.(get|post|put|delete|patch|head|session)\b",
    r"\burllib\.request\b",
    r"\burllib\.urlopen\b",
    r"\bhttp\.client\b",
    r"\bhttpx\.",
    r"\baiohttp\.",
    r"\bfetch\s*\(",
    r"\bcurl\b",
    r"\bwget\b",
]


# ──────────────────────────────────────────────
# セキュリティチェックルール定義
# ──────────────────────────────────────────────

# ハードコードされた認証情報のパターン
_CREDENTIAL_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}', "API キー"),
    (r'(?i)(secret|password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}', "パスワード/シークレット"),
    (r'(?i)(token)\s*[=:]\s*["\']?[A-Za-z0-9_\-\.]{20,}', "トークン"),
    (r'(?i)Authorization\s*[=:]\s*["\']?Bearer\s+[A-Za-z0-9_\-\.]+', "Bearer トークン"),
    (r'(?i)(access[_-]?key|private[_-]?key)\s*[=:]\s*["\']?[A-Za-z0-9/+=]{20,}', "アクセスキー"),
]

# 敵対的指示パターン（安全ルールの迂回・ユーザーへの隠蔽・データ流出）
_ADVERSARIAL_PATTERNS = [
    (r'(?i)(ignore|bypass|override|disregard)\s+(safety|security|rule|guideline|restriction|filter)', "安全ルールの迂回指示"),
    (r'(?i)hide\s+(this|from\s+(the\s+)?user|action)', "ユーザーへの隠蔽指示"),
    (r'(?i)do\s+not\s+(tell|show|reveal|disclose|inform)', "情報開示拒否指示"),
    (r'(?i)without\s+(the\s+)?user.{0,10}knowledge', "ユーザー非認知操作"),
    (r'(?i)exfiltrat', "データ流出指示"),
    (r'安全.{0,10}(無視|迂回|バイパス)', "安全ルール無視指示（日本語）"),
    (r'ユーザー.{0,15}(隠|知らせ|非表示)', "ユーザー隠蔽指示（日本語）"),
]

# MCP サーバー参照パターン（ServerName:tool_name 形式）
_MCP_PATTERN = r'\b([A-Z][a-zA-Z0-9]+):([a-z][a-z0-9_]+)\b'

# 外部 URL パターン（localhost 除外）
_EXTERNAL_URL_PATTERN = r'https?://(?!localhost\b|127\.0\.0\.1\b)[^\s\)\]"\'`]+'

# パストラバーサルパターン
_PATH_TRAVERSAL_PATTERN = r'\.\.[/\\]'

# 広範な glob パターン
_BROAD_GLOB_PATTERN = r'(\*\*/\*|(?<!\w)\*\*$|\*\*\s|/\*[^.])'

# データ流出パターン（機密読み取り後に外部送信）
_EXFIL_READ_PATTERNS = [
    r'\b(open|read|cat|get_contents?)\b.{0,100}\b(password|secret|key|token|credential)',
]
_EXFIL_SEND_PATTERNS = [
    r'\b(requests\.(post|put)|urllib|curl|wget|send|upload|transmit)\b',
]


# ──────────────────────────────────────────────
# 品質チェック関数
# ──────────────────────────────────────────────

def check_name(name: str) -> list[dict]:
    issues = []
    for word in _RESERVED_WORDS:
        if word in name.lower():
            issues.append({
                "severity": "error",
                "code": "NAME_RESERVED_WORD",
                "message": f"name に予約語 '{word}' が含まれています",
            })
    name_parts = set(name.lower().replace("-", " ").split())
    if name.lower() in _AMBIGUOUS_NAMES or name_parts & _AMBIGUOUS_NAMES == name_parts:
        issues.append({
            "severity": "warning",
            "code": "NAME_AMBIGUOUS",
            "message": f"name '{name}' が曖昧または汎用的すぎます。より具体的な名前を推奨します",
        })
    return issues


def check_description(desc: str) -> list[dict]:
    issues = []
    if re.search(r"<[a-zA-Z/]", desc):
        issues.append({
            "severity": "error",
            "code": "DESC_XML_TAG",
            "message": "description に XML タグが含まれています",
        })
    for pattern in _FIRST_PERSON_PATTERNS:
        if re.search(pattern, desc):
            issues.append({
                "severity": "warning",
                "code": "DESC_FIRST_PERSON",
                "message": "description が一人称で書かれている可能性があります。三人称（「〜する」「〜を行う」）で記述してください",
            })
            break
    has_trigger = any(re.search(p, desc) for p in _TRIGGER_PATTERNS)
    if not has_trigger:
        issues.append({
            "severity": "warning",
            "code": "DESC_NO_TRIGGER",
            "message": "description にスキル発動のトリガー条件（「〜の場合」「〜とき」「〜などで発動」等）が含まれていません",
        })
    return issues


def check_metadata_version(fm: dict) -> list[dict]:
    issues = []
    metadata = fm.get("metadata")
    if not isinstance(metadata, dict):
        issues.append({
            "severity": "warning",
            "code": "META_NO_VERSION",
            "message": 'metadata.version が未設定です。フロントマターに metadata: / version: "1.0" を追加してください',
        })
    elif "version" not in metadata:
        issues.append({
            "severity": "warning",
            "code": "META_NO_VERSION",
            "message": "metadata.version が未設定です",
        })
    return issues


def check_body(body: str, skill_dir: str) -> list[dict]:
    issues = []
    lines = body.splitlines()
    if len(lines) > 500:
        issues.append({
            "severity": "warning",
            "code": "BODY_TOO_LONG",
            "message": f"SKILL.md 本文が {len(lines)} 行あります（推奨: 500 行以下）。references/ への分割を検討してください",
        })
    if re.search(r'(?:scripts|references|assets)\\', body):
        issues.append({
            "severity": "warning",
            "code": "PATH_BACKSLASH",
            "message": "ファイルパスにバックスラッシュが使われています。フォワードスラッシュ（/）を使用してください",
        })
    ref_links = re.findall(r'\[.*?\]\(([\w./\-]+\.md)\)', body)
    checked_refs: set[str] = set()
    for ref in ref_links:
        if ref in checked_refs:
            continue
        checked_refs.add(ref)
        ref_path = os.path.join(skill_dir, ref)
        if not os.path.isfile(ref_path):
            continue
        with open(ref_path, encoding="utf-8", errors="replace") as f:
            ref_content = f.read()
        ref_lines = ref_content.splitlines()
        if len(ref_lines) >= 100:
            has_toc = any(
                re.search(r'^#{1,3}\s*(目次|Contents?|Table of Contents)', line)
                for line in ref_lines[:20]
            )
            if not has_toc:
                issues.append({
                    "severity": "warning",
                    "code": "REF_NO_TOC",
                    "message": f"{ref} は {len(ref_lines)} 行ありますが先頭に目次（## 目次）がありません",
                })
        nested_refs = re.findall(r'\[.*?\]\(([\w./\-]+\.md)\)', ref_content)
        if nested_refs:
            issues.append({
                "severity": "warning",
                "code": "REF_NESTED",
                "message": f"{ref} がさらに他のファイルを参照しています（推奨: SKILL.md から 1 階層のみ）",
            })
    return issues


def check_scripts(skill_dir: str) -> list[dict]:
    issues = []
    scripts_dir = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts_dir):
        return issues

    def strip_python_literals(content: str) -> str:
        """Python の文字列/コメントを除去して誤検知を抑える。"""
        tokens: list[tuple[int, str]] = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(content).readline):
                if token.type in (tokenize.STRING, tokenize.COMMENT):
                    continue
                tokens.append((token.type, token.string))
            return tokenize.untokenize(tokens)
        except (tokenize.TokenError, IndentationError):
            return content

    for fname in sorted(os.listdir(scripts_dir)):
        if not fname.endswith((".py", ".sh")):
            continue
        fpath = os.path.join(scripts_dir, fname)
        with open(fpath, encoding="utf-8", errors="replace") as f:
            content = f.read()
        if fname.endswith(".py"):
            content = strip_python_literals(content)
        for pattern in _NETWORK_PATTERNS:
            if re.search(pattern, content):
                issues.append({
                    "severity": "warning",
                    "code": "SCRIPT_NETWORK",
                    "message": f"scripts/{fname} にネットワーク呼び出しの可能性があります（意図的な場合は無視してください）",
                })
                break
    return issues


# ──────────────────────────────────────────────
# セキュリティチェック関数
# ──────────────────────────────────────────────

def _collect_all_text(skill_dir: str) -> dict[str, str]:
    """スキルディレクトリ内の全テキストファイルを収集する。"""
    texts: dict[str, str] = {}
    for root, _, files in os.walk(skill_dir):
        for fname in files:
            if fname.endswith((".md", ".py", ".sh", ".js", ".txt", ".yaml", ".yml", ".json")):
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, skill_dir)
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as f:
                        texts[rel] = f.read()
                except OSError:
                    pass
    return texts


def security_check(skill_dir: str) -> list[dict]:
    """セキュリティリスクを検出して返す。評価基準には影響しない。

    Returns:
        list of {"level": "HIGH"|"MEDIUM", "code": str, "message": str}
    """
    risks: list[dict] = []
    texts = _collect_all_text(skill_dir)

    skill_md_content = texts.get("SKILL.md", "")
    _, skill_body = parse_frontmatter(skill_md_content)

    # ── HIGH: ハードコードされた認証情報 ──────────────────────────
    for rel, content in texts.items():
        for pattern, cred_type in _CREDENTIAL_PATTERNS:
            if re.search(pattern, content):
                risks.append({
                    "level": "HIGH",
                    "code": "SEC_HARDCODED_CREDENTIAL",
                    "message": f"{rel} にハードコードされた {cred_type} が疑われます。環境変数を使用してください",
                })
                break  # 1ファイル1件

    # ── HIGH: 敵対的指示（SKILL.md と参照 .md のみ対象）──────────
    md_texts = {k: v for k, v in texts.items() if k.endswith(".md")}
    for rel, content in md_texts.items():
        for pattern, label in _ADVERSARIAL_PATTERNS:
            if re.search(pattern, content):
                risks.append({
                    "level": "HIGH",
                    "code": "SEC_ADVERSARIAL_INSTRUCTION",
                    "message": f"{rel} に {label} のパターンが検出されました。内容を確認してください",
                })
                break

    # ── HIGH: 外部 URL（SKILL.md とスクリプトのみ対象）──────────
    # 参照ドキュメント（references/ 等）内のドキュメント用 URL は対象外
    url_check_texts = {
        k: v for k, v in texts.items()
        if k == "SKILL.md" or k.startswith("scripts/") or k.startswith("scripts" + os.sep)
    }
    seen_url_domains: set[tuple[str, str]] = set()
    for rel, content in url_check_texts.items():
        urls = re.findall(_EXTERNAL_URL_PATTERN, content)
        for url in urls:
            domain_m = re.match(r'https?://([^/\s]+)', url)
            key = (rel, domain_m.group(1) if domain_m else url)
            if key in seen_url_domains:
                continue
            seen_url_domains.add(key)
            risks.append({
                "level": "HIGH",
                "code": "SEC_EXTERNAL_URL",
                "message": f"{rel} に外部 URL が含まれています: {url[:80]}",
            })

    # ── HIGH: ネットワークアクセス（スクリプト）─────────────────
    script_texts = {k: v for k, v in texts.items()
                    if k.startswith("scripts" + os.sep) or k.startswith("scripts/")}
    for rel, content in script_texts.items():
        for pattern in _NETWORK_PATTERNS:
            if re.search(pattern, content):
                risks.append({
                    "level": "HIGH",
                    "code": "SEC_SCRIPT_NETWORK",
                    "message": f"{rel} にネットワーク呼び出しがあります（データ流出ベクトルになりえます）",
                })
                break

    # ── HIGH: データ流出パターン（読み取り→送信の組み合わせ）────
    for rel, content in script_texts.items():
        has_read = any(re.search(p, content, re.IGNORECASE) for p in _EXFIL_READ_PATTERNS)
        has_send = any(re.search(p, content, re.IGNORECASE) for p in _EXFIL_SEND_PATTERNS)
        if has_read and has_send:
            risks.append({
                "level": "HIGH",
                "code": "SEC_DATA_EXFILTRATION",
                "message": f"{rel} で機密データの読み取りと外部送信のパターンが共存しています",
            })

    # ── HIGH: MCP サーバー参照（SKILL.md）───────────────────────
    mcp_refs = re.findall(_MCP_PATTERN, skill_body)
    if mcp_refs:
        refs_str = ", ".join(f"{s}:{t}" for s, t in mcp_refs[:3])
        risks.append({
            "level": "HIGH",
            "code": "SEC_MCP_REFERENCE",
            "message": f"SKILL.md に MCP サーバー参照があります: {refs_str}（スキル外のアクセス拡張）",
        })

    # ── MEDIUM: パストラバーサル ──────────────────────────────────
    for rel, content in texts.items():
        if re.search(_PATH_TRAVERSAL_PATTERN, content):
            risks.append({
                "level": "MEDIUM",
                "code": "SEC_PATH_TRAVERSAL",
                "message": f"{rel} にパストラバーサル（../）が含まれています。意図しないファイルアクセスが発生する可能性があります",
            })

    # ── MEDIUM: 広範な glob パターン（スクリプトのみ対象）────────
    # .md ファイルは markdown の **太字** と混同するため除外
    non_md_texts = {k: v for k, v in texts.items() if not k.endswith(".md")}
    for rel, content in non_md_texts.items():
        if re.search(_BROAD_GLOB_PATTERN, content):
            risks.append({
                "level": "MEDIUM",
                "code": "SEC_BROAD_GLOB",
                "message": f"{rel} に広範な glob パターン（**/* 等）があります。意図しないファイルにアクセスする可能性があります",
            })

    # ── MEDIUM: コード実行スクリプトの存在 ───────────────────────
    exec_scripts = [
        k for k in texts
        if (k.startswith("scripts/") or k.startswith("scripts" + os.sep))
        and any(k.endswith(ext) for ext in (".py", ".sh", ".js"))
    ]
    if exec_scripts:
        risks.append({
            "level": "MEDIUM",
            "code": "SEC_SCRIPT_EXISTS",
            "message": f"実行可能スクリプトが {len(exec_scripts)} 件あります: {', '.join(exec_scripts[:5])}（完全な環境アクセスで実行されます）",
        })

    return risks


# ──────────────────────────────────────────────
# メイン評価ロジック
# ──────────────────────────────────────────────

def check_skill(skill_dir: str) -> dict:
    """スキルディレクトリを検査して結果を返す。"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return {
            "name": os.path.basename(skill_dir),
            "errors": [{"severity": "error", "code": "NO_SKILL_MD", "message": "SKILL.md が見つかりません"}],
            "warnings": [],
            "security_risks": [],
        }

    with open(skill_md, encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    all_issues: list[dict] = []

    name = fm.get("name", "")
    desc = fm.get("description", "")

    if name:
        all_issues.extend(check_name(name))
    if desc:
        all_issues.extend(check_description(desc))

    all_issues.extend(check_metadata_version(fm))
    all_issues.extend(check_body(body, skill_dir))
    all_issues.extend(check_scripts(skill_dir))

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    security_risks = security_check(skill_dir)

    return {
        "name": name or os.path.basename(skill_dir),
        "errors": errors,
        "warnings": warnings,
        "security_risks": security_risks,
    }


def find_skill_dirs(base_dir: str) -> list[str]:
    """ベースディレクトリ以下のスキルディレクトリを返す。"""
    if not os.path.isdir(base_dir):
        return []
    return [
        os.path.join(base_dir, entry)
        for entry in sorted(os.listdir(base_dir))
        if os.path.isdir(os.path.join(base_dir, entry))
        and os.path.isfile(os.path.join(base_dir, entry, "SKILL.md"))
    ]


def print_results(results: list[dict]) -> int:
    """品質チェック結果を表示してエラー件数を返す。"""
    total_errors = 0
    total_warnings = 0

    print("── 品質チェック ─────────────────────────────\n")
    for r in results:
        errors = r["errors"]
        warnings = r["warnings"]
        total_errors += len(errors)
        total_warnings += len(warnings)

        if not errors and not warnings:
            print(f"  ✅ {r['name']}")
            continue

        status = "❌" if errors else "⚠️ "
        print(f"  {status} {r['name']}")
        for e in errors:
            print(f"      [ERROR] {e['message']}")
        for w in warnings:
            print(f"      [WARN]  {w['message']}")

    print()
    print(f"品質: {len(results)} スキル / エラー {total_errors} 件 / 警告 {total_warnings} 件")

    # セキュリティリスクは別セクションで報告（評価基準に影響しない）
    skills_with_risks = [r for r in results if r.get("security_risks")]
    if skills_with_risks:
        print()
        print("── セキュリティリスク（参考情報）────────────\n")
        print("  ※ 以下はリスクの報告です。修正するかどうかはレビュアーが判断してください。\n")
        total_high = 0
        total_medium = 0
        for r in skills_with_risks:
            risks = r["security_risks"]
            high = [x for x in risks if x["level"] == "HIGH"]
            medium = [x for x in risks if x["level"] == "MEDIUM"]
            total_high += len(high)
            total_medium += len(medium)
            print(f"  🔒 {r['name']}")
            for risk in high:
                print(f"      [HIGH]   {risk['message']}")
            for risk in medium:
                print(f"      [MEDIUM] {risk['message']}")
        print()
        print(f"セキュリティ: HIGH {total_high} 件 / MEDIUM {total_medium} 件")

    return total_errors


def main() -> None:
    parser = argparse.ArgumentParser(description="スキルの静的品質チェック")
    parser.add_argument("--skill", help="特定スキルのみチェック（スキル名）")
    parser.add_argument(
        "--path",
        default=".github/skills",
        help="スキルのベースディレクトリ (default: .github/skills)",
    )
    args = parser.parse_args()

    print("🔍 スキル品質チェック\n")

    if args.skill:
        skill_dir = os.path.join(args.path, args.skill)
        if not os.path.isdir(skill_dir):
            home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
            skill_dir = os.path.join(home, ".copilot", "skills", args.skill)
            if not os.path.isdir(skill_dir):
                print(f"[ERROR] スキル '{args.skill}' が見つかりません")
                sys.exit(1)
        dirs = [skill_dir]
    else:
        dirs = find_skill_dirs(args.path)
        if not dirs:
            print(f"スキルが見つかりません: {args.path}")
            sys.exit(0)

    results = [check_skill(d) for d in dirs]
    error_count = print_results(results)
    sys.exit(1 if error_count > 0 else 0)


if __name__ == "__main__":
    main()
