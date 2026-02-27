#!/usr/bin/env python3
"""スキルの CHANGELOG.md を git ログとフロントマターのバージョン変更から自動生成する。

使い方:
    python changelog.py <skill_name>              # CHANGELOG.md を生成・上書き
    python changelog.py <skill_name> --dry-run    # 内容を標準出力に出力（ファイル変更なし）
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _skill_path(skill_name: str) -> str | None:
    """ワークスペース → インストール済みの順でスキルパスを返す。

    ワークスペースパスはリポジトリルートを基準に構築する。
    """
    root = _repo_root()
    workspace = os.path.join(root, ".github", "skills", skill_name)
    if os.path.isdir(workspace):
        return workspace
    sys.path.insert(0, os.path.dirname(__file__))
    from registry import _skill_home
    installed = os.path.join(_skill_home(), skill_name)
    if os.path.isdir(installed):
        return installed
    return None


def _parse_version(content: str) -> str | None:
    """SKILL.md のフロントマターから metadata.version を取り出す。"""
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        return None
    in_metadata = False
    for line in fm_match.group(1).splitlines():
        if line.startswith("metadata:"):
            in_metadata = True
            continue
        if in_metadata:
            if line and not line[0].isspace():
                in_metadata = False
            elif line.lstrip().startswith("version:"):
                return line.split(":", 1)[1].strip().strip("\"'") or None
    return None


def _version_at_commit(commit_hash: str, skill_md_rel: str, repo_root: str) -> str | None:
    """指定コミット時点の SKILL.md からバージョンを読み取る。"""
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:{skill_md_rel}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return _parse_version(result.stdout)


def _get_commits(rel_path: str, repo_root: str) -> list[dict]:
    """スキルに関連するコミット一覧を新しい順で返す。"""
    result = subprocess.run(
        ["git", "log", "--follow", "--format=%aI|%h|%s", "--", rel_path],
        cwd=repo_root, capture_output=True, text=True,
    )
    commits = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "date": parts[0][:10],
                "hash": parts[1],
                "subject": parts[2],
            })
    return commits  # 新しい順


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def generate_changelog(skill_name: str) -> str:
    """CHANGELOG.md の文字列を生成して返す。

    フロントマターの version フィールドが変わったタイミングでセクションを区切る。
    バージョン未記載のコミットは "Unreleased" セクションにまとめる。
    """
    root = _repo_root()
    path = _skill_path(skill_name)
    if not path:
        raise FileNotFoundError(f"スキル '{skill_name}' が見つかりません")

    rel_path = os.path.relpath(path, root).replace("\\", "/")
    skill_md_rel = f"{rel_path}/SKILL.md"

    commits = _get_commits(rel_path, root)
    if not commits:
        return "# Changelog\n\n(変更履歴なし)\n"

    # コミットをフロントマターのバージョン変化点でグループ化（新しい順）
    sections: list[dict] = []   # {"version": str, "date": str, "entries": [str]}
    current_version: str | None = None
    current_entries: list[str] = []
    current_date: str | None = None

    for commit in commits:
        ver = _version_at_commit(commit["hash"], skill_md_rel, root) or "unreleased"

        if ver != current_version:
            # バージョンが変化 → 手前のグループを確定
            if current_entries:
                sections.append({
                    "version": current_version,
                    "date": current_date,
                    "entries": current_entries,
                })
            current_version = ver
            current_entries = []
            current_date = commit["date"]

        current_entries.append(f"- {commit['subject']} (`{commit['hash']}`)")

    # 末尾グループをフラッシュ
    if current_entries:
        sections.append({
            "version": current_version,
            "date": current_date,
            "entries": current_entries,
        })

    # Markdown 組み立て
    lines = ["# Changelog", ""]
    for sec in sections:
        ver = sec["version"]
        date = sec["date"] or ""
        heading = f"## Unreleased — {date}" if ver == "unreleased" else f"## {ver} — {date}"
        lines.append(heading)
        lines.append("")
        lines.extend(sec["entries"])
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="スキルの CHANGELOG.md をフロントマターのバージョン変更単位で自動生成する"
    )
    parser.add_argument("skill_name", help="スキル名")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="ファイルに書かず標準出力に出力する",
    )
    args = parser.parse_args()

    content = generate_changelog(args.skill_name)

    if args.dry_run:
        print(content)
        return

    path = _skill_path(args.skill_name)
    out = os.path.join(path, "CHANGELOG.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ {out} を生成しました")


if __name__ == "__main__":
    main()
