#!/usr/bin/env python3
"""
evolve.py — 進化候補の procedural 記憶を改善し、ltm-use に保存して GitLab PR を作成する。

使い方:
  python evolve.py <memory_id>              # 指定 ID の記憶を進化
  python evolve.py --auto                   # analyze.py の上位候補を自動処理
  python evolve.py <memory_id> --dry-run    # PR 作成せずに改善案を表示

フロー:
  1. analyze.py で進化候補を取得
  2. kiro-cli に既存手順 + 改善ヒントを渡して改善バリアントを生成
  3. ltm-use に新バージョンとして保存（元記憶は archived に）
  4. GitLab に skill-evolution ブランチを作成して PR を作成（auto-merge なし）

環境変数:
  GITLAB_TOKEN   GitLab パーソナルアクセストークン（PR 作成時に必須）
  GL_SCRIPT      gl.py のパス（省略時: 自動検索）
  KIRO_CLI       kiro-cli のパス（省略時: kiro-cli）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MEMORY_HOME = Path.home() / ".kiro/memory/home"
_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _find_script(name: str, candidates: list[Path]) -> str:
    env = os.environ.get(name.upper().replace("-", "_").replace(".", "_"))
    if env and Path(env).exists():
        return env
    for c in candidates:
        if c.exists():
            return str(c)
    return name  # PATH に任せる


def _find_gl() -> str:
    return _find_script("GL_SCRIPT", [
        Path(__file__).parent.parent / ".github/skills/gitlab-idd/scripts/gl.py",
        Path.home() / ".kiro/skills/gitlab-idd/scripts/gl.py",
    ])


def _find_ltm_save() -> str:
    return _find_script("LTM_SAVE", [
        Path.home() / ".kiro/skills/ltm-use/scripts/save_memory.py",
    ])


def _gl(gl_script: str, *args: str) -> str:
    r = subprocess.run([sys.executable, gl_script, *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: gl.py {' '.join(args)}\n{r.stderr}")
    return r.stdout.strip()


def _parse_fm(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        result[k.strip()] = v.strip().strip('"')
    return result


def _load_memory(path: str) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    fm = _parse_fm(text)
    return {**fm, "path": str(p), "body": text}


def _find_memory_by_id(mem_id: str) -> dict | None:
    for md in MEMORY_HOME.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_fm(text)
        if fm.get("id") == mem_id:
            return {**fm, "path": str(md), "body": text}
    return None


# ---------------------------------------------------------------------------
# kiro-cli で改善バリアントを生成
# ---------------------------------------------------------------------------

EVOLVE_PROMPT_TEMPLATE = """\
あなたは AI エージェントの手順書（procedural 記憶）を改善するエキスパートです。

## 現在の手順書

{current_body}

## 改善の理由

{reasons}

## 改善タスク

以下の観点で手順書を改善してください:
1. ステップ数を削減できる箇所を特定して簡略化する
2. エラーハンドリング・注意事項を強化する
3. 前提条件を明確化する

改善版の手順書を Markdown 形式で出力してください。
必ず「## 変更履歴」セクションを末尾に追加し、変更点を箇条書きで記述してください。
フロントマターは含めないでください（本文のみ出力）。
"""


def _generate_variant(memory: dict, reasons: list[str], dry_run: bool) -> str:
    """kiro-cli を使って改善バリアントを生成する。dry_run 時はプロンプトを表示して終了。"""
    prompt = EVOLVE_PROMPT_TEMPLATE.format(
        current_body=memory["body"],
        reasons="\n".join(f"- {r}" for r in reasons),
    )

    if dry_run:
        print("=== [DRY RUN] kiro-cli に渡すプロンプト ===")
        print(prompt[:1000], "..." if len(prompt) > 1000 else "")
        return ""

    kiro = os.environ.get("KIRO_CLI", "kiro-cli")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        r = subprocess.run(
            [kiro, "chat", "--trust-all-tools", f"@{prompt_file}"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            print(f"[evolve] WARN: kiro-cli が失敗しました。手動で改善してください。\n{r.stderr[:500]}")
            return ""
        return r.stdout.strip()
    except FileNotFoundError:
        print("[evolve] WARN: kiro-cli が見つかりません。改善バリアントの生成をスキップします。")
        return ""
    finally:
        os.unlink(prompt_file)


# ---------------------------------------------------------------------------
# ltm-use に保存
# ---------------------------------------------------------------------------

def _save_variant(memory: dict, variant_body: str, reasons: list[str]) -> str:
    """改善バリアントを ltm-use に保存して新しい記憶 ID を返す。"""
    ltm_save = _find_ltm_save()
    old_title = memory.get("title", "")
    new_title = re.sub(r"\s*\(v\d+\.\d+\)$", "", old_title) + " (evolved)"

    summary = memory.get("summary", "")
    improvement = "; ".join(reasons[:2]) if reasons else "自動進化"

    r = subprocess.run(
        [
            sys.executable, ltm_save,
            "--no-dedup", "--no-auto-tags",
            "--scope", "home",
            "--category", memory.get("category", "general"),
            "--title", new_title,
            "--summary", f"{summary} [改善: {improvement}]",
            "--content", variant_body,
            "--tags", "procedural,evolved",
            "--context", f"進化元: {memory.get('id', '')}",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"ERROR: ltm-use save\n{r.stderr}")
    print(r.stdout.strip())

    # 保存された ID を stdout から抽出
    m = re.search(r"mem-\d{8}-\d+", r.stdout)
    return m.group(0) if m else ""


def _archive_original(memory: dict) -> None:
    """元の記憶を archived にする。"""
    ltm_save = _find_ltm_save()
    subprocess.run(
        [
            sys.executable, ltm_save,
            "--update", memory["path"],
            "--status", "archived",
        ],
        capture_output=True, text=True,
    )
    print(f"[evolve] 元記憶を archived に更新: {memory.get('id', '')}")


# ---------------------------------------------------------------------------
# GitLab PR 作成
# ---------------------------------------------------------------------------

PR_BODY_TEMPLATE = """\
## 🧬 スキル進化レポート

**記憶タイトル**: {title}
**進化元 ID**: {old_id}
**新 ID**: {new_id}

### 改善理由

{reasons}

### 変更概要

{variant_summary}

### ⚠️ レビュー必須項目

- [ ] 改善根拠が妥当か
- [ ] 既存タスクへの後方互換性
- [ ] 意図しない動作変更がないか

> このPRは自動進化パイプラインによって作成されました。
> **auto-merge は設定されていません。** 内容を確認してからマージしてください。
"""


def _create_pr(memory: dict, new_id: str, reasons: list[str], variant_body: str) -> None:
    gl = _find_gl()

    # デフォルトブランチを取得
    default_branch = _gl(gl, "get-default-branch", "--get", "default_branch")
    branch = f"skill-evolution/{memory.get('id', 'unknown')}"

    # ブランチ作成
    r = subprocess.run(
        ["git", "fetch", "origin", default_branch],
        capture_output=True, text=True,
    )
    r = subprocess.run(
        ["git", "checkout", "-b", branch, f"origin/{default_branch}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        # ブランチが既に存在する場合はスキップ
        subprocess.run(["git", "checkout", branch], capture_output=True, text=True)

    # 変更をコミット（evolution ログファイルを追加）
    log_path = Path("evolution/log") / f"{memory.get('id', 'unknown')}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"# 進化ログ: {memory.get('title', '')}\n\n"
        f"- 進化元: {memory.get('id', '')}\n"
        f"- 新 ID: {new_id}\n"
        f"- 理由: {'; '.join(reasons)}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(log_path)], capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", f"🧬 skill-evolution: {memory.get('title', '')}"],
        capture_output=True, text=True,
    )
    subprocess.run(["git", "push", "-u", "origin", branch], capture_output=True, text=True)

    # PR 本文
    variant_summary = "\n".join(
        f"- {line.lstrip('- ').strip()}"
        for line in variant_body.splitlines()
        if line.strip().startswith("-") or line.strip().startswith("*")
    )[:500] or "(改善バリアントを確認してください)"

    pr_body = PR_BODY_TEMPLATE.format(
        title=memory.get("title", ""),
        old_id=memory.get("id", ""),
        new_id=new_id,
        reasons="\n".join(f"- {r}" for r in reasons),
        variant_summary=variant_summary,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(pr_body)
        body_file = f.name

    try:
        result = _gl(
            gl,
            "create-mr",
            "--title", f"🧬 [skill-evolution] {memory.get('title', '')}",
            "--source-branch", branch,
            "--target-branch", default_branch,
            "--description-file", body_file,
        )
        mr = json.loads(result)
        print(f"[evolve] PR を作成しました: {mr.get('web_url', '')}")
    finally:
        os.unlink(body_file)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def evolve(mem_id: str, dry_run: bool = False, skip_pr: bool = False) -> None:
    memory = _find_memory_by_id(mem_id)
    if not memory:
        sys.exit(f"ERROR: 記憶が見つかりません: {mem_id}")

    # analyze.py から理由を取得
    from analyze import analyze
    candidates = analyze(min_score=0)
    reasons = next(
        (c["reasons"] for c in candidates if c["id"] == mem_id),
        ["手動指定による進化"],
    )

    print(f"[evolve] 対象: {memory.get('title', '')} ({mem_id})")
    print(f"[evolve] 理由: {'; '.join(reasons) or 'なし'}")

    # バリアント生成
    variant_body = _generate_variant(memory, reasons, dry_run)
    if dry_run:
        return

    if not variant_body:
        print("[evolve] バリアントが生成されませんでした。処理を中断します。")
        return

    # ltm-use に保存
    new_id = _save_variant(memory, variant_body, reasons)
    print(f"[evolve] 改善バリアントを保存しました: {new_id}")

    # 元記憶を archived に
    _archive_original(memory)

    # GitLab PR 作成
    if not skip_pr:
        try:
            _create_pr(memory, new_id, reasons, variant_body)
        except Exception as e:
            print(f"[evolve] WARN: PR 作成に失敗しました（{e}）。手動で作成してください。")
    else:
        print("[evolve] --skip-pr が指定されたため PR 作成をスキップしました。")


def main() -> None:
    parser = argparse.ArgumentParser(description="procedural 記憶を進化させる")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("mem_id", nargs="?", help="進化させる記憶 ID")
    group.add_argument("--auto", action="store_true", help="analyze.py の上位候補を自動処理")
    parser.add_argument("--dry-run", action="store_true", help="PR 作成せずに改善プロンプトを表示")
    parser.add_argument("--skip-pr", action="store_true", help="ltm-use 保存のみ行い PR を作成しない")
    args = parser.parse_args()

    if args.auto:
        from analyze import analyze
        candidates = analyze()
        if not candidates:
            print("進化候補はありません。")
            return
        top = candidates[0]
        print(f"[evolve] 上位候補を処理: {top['title']} (score={top['evolution_score']:.0f})")
        evolve(top["id"], dry_run=args.dry_run, skip_pr=args.skip_pr)
    else:
        evolve(args.mem_id, dry_run=args.dry_run, skip_pr=args.skip_pr)


if __name__ == "__main__":
    main()
