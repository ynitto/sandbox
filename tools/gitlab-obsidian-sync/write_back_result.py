#!/usr/bin/env python3
"""
write_back_result.py — GitLab Issue の結果を Obsidian ノートに書き戻し、
完了タスクを Archive/ フォルダへ移動する。

使い方:
  python write_back_result.py <task_id> <vault_path>
  python write_back_result.py <task_id> <vault_path> [--note <note_path>]

環境変数:
  GITLAB_TOKEN   GitLab パーソナルアクセストークン（必須）
  GL_SCRIPT      gl.py のパス（省略時: 自動検索）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# フロントマター解析・更新（classify_task.py と共通ロジック）
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


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


def _update_fm(text: str, updates: dict) -> str:
    m = _FM_RE.match(text)
    if not m:
        return text
    lines = m.group(1).splitlines()
    updated_keys: set = set()
    new_lines = []
    for line in lines:
        if ":" not in line:
            new_lines.append(line)
            continue
        k = line.split(":")[0].strip()
        if k in updates:
            new_lines.append(f"{k}: {updates[k]}")
            updated_keys.add(k)
        else:
            new_lines.append(line)
    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}: {v}")
    return f"---\n{chr(10).join(new_lines)}\n---\n" + text[m.end():]


# ---------------------------------------------------------------------------
# gl.py の検索
# ---------------------------------------------------------------------------

def _find_gl_script() -> str:
    env = os.environ.get("GL_SCRIPT")
    if env and Path(env).exists():
        return env
    candidates = [
        Path(__file__).parent.parent / ".github/skills/gitlab-idd/scripts/gl.py",
        Path.home() / ".kiro/skills/gitlab-idd/scripts/gl.py",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    sys.exit("ERROR: gl.py が見つかりません。GL_SCRIPT 環境変数で指定してください。")


def _gl(gl_script: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, gl_script, *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"ERROR: gl.py {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Issue の結果を取得
# ---------------------------------------------------------------------------

def _get_result_from_issue(gl_script: str, task_id: str) -> tuple[str, str, str]:
    """(state, result_summary, skill_ref) を返す。"""
    issue = json.loads(_gl(gl_script, "get-issue", task_id))
    state = issue.get("state", "opened")

    # description の ## 実行結果 セクションを抽出
    desc = issue.get("description") or ""
    result_m = re.search(r"## 実行結果\n(.*?)(?=\n##|\Z)", desc, re.DOTALL)
    result_summary = result_m.group(1).strip() if result_m else ""

    # コメントから最新のサマリーを取得（ワーカーが投稿した ✅ 実装完了 コメント）
    if not result_summary:
        comments = json.loads(_gl(gl_script, "get-comments", task_id))
        for c in reversed(comments):
            body = c.get("body", "")
            if "✅" in body or "実装完了" in body:
                # 最初の非空行を要約として使用
                for line in body.splitlines():
                    line = line.strip().lstrip("#").strip()
                    if line and not line.startswith("<!--"):
                        result_summary = line[:200]
                        break
                break

    # skill_ref: コメントに記録されていれば取得（将来拡張用）
    skill_ref = ""

    return state, result_summary, skill_ref


# ---------------------------------------------------------------------------
# ノートを検索
# ---------------------------------------------------------------------------

def _find_note(vault_path: Path, task_id: str) -> Path | None:
    """vault 内で frontmatter の task_id が一致するノートを検索。"""
    for md in vault_path.rglob("*.md"):
        if "Archive" in md.parts:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_fm(text)
        if str(fm.get("task_id", "")) == str(task_id):
            return md
    return None


# ---------------------------------------------------------------------------
# ノートへの書き戻し
# ---------------------------------------------------------------------------

def _write_back(note_path: Path, result_summary: str, skill_ref: str, state: str) -> None:
    text = note_path.read_text(encoding="utf-8")

    # ## 実行結果 セクションに追記
    if "## 実行結果" in text:
        text = re.sub(
            r"(## 実行結果\n)(<!-- 自動追記.*?-->\n?)?",
            f"## 実行結果\n{result_summary}\n\n",
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        text += f"\n## 実行結果\n{result_summary}\n"

    # ## 参考スキル セクションに skill_ref を追記
    if skill_ref:
        skill_link = f"- [[{skill_ref}]]"
        if "## 参考スキル" in text:
            text = re.sub(
                r"(## 参考スキル\n)(<!-- 自動追記.*?-->\n?)?",
                f"## 参考スキル\n{skill_link}\n\n",
                text,
                count=1,
                flags=re.DOTALL,
            )

    # フロントマターの status を更新
    new_status = "Done" if state == "closed" else "In Progress"
    text = _update_fm(text, {"status": new_status})

    note_path.write_text(text, encoding="utf-8")
    print(f"[write_back] ノートを更新しました: {note_path}")


# ---------------------------------------------------------------------------
# Archive 移動
# ---------------------------------------------------------------------------

def _archive(note_path: Path, vault_path: Path) -> Path:
    archive_dir = vault_path / "Archive"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / note_path.name
    # 同名ファイルが存在する場合はサフィックスを付ける
    if dest.exists():
        stem = note_path.stem
        suffix = note_path.suffix
        i = 1
        while dest.exists():
            dest = archive_dir / f"{stem}-{i}{suffix}"
            i += 1
    note_path.rename(dest)
    print(f"[write_back] Archive に移動しました: {dest}")
    return dest


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def write_back_to_obsidian(task_id: str, vault_path: str, note_path: str | None = None) -> None:
    vault = Path(vault_path).expanduser()
    if not vault.is_dir():
        sys.exit(f"ERROR: vault が見つかりません: {vault_path}")

    gl_script = _find_gl_script()
    state, result_summary, skill_ref = _get_result_from_issue(gl_script, task_id)

    # ノートを特定
    if note_path:
        note = Path(note_path)
    else:
        note = _find_note(vault, task_id)

    if not note or not note.exists():
        sys.exit(f"ERROR: task_id={task_id} に対応するノートが見つかりません。--note で指定してください。")

    _write_back(note, result_summary, skill_ref, state)

    # Issue がクローズ済みなら Archive に移動
    if state == "closed":
        _archive(note, vault)
    else:
        print(f"[write_back] Issue #{task_id} はまだオープンです（state={state}）。Archive 移動をスキップ。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GitLab Issue の結果を Obsidian ノートに書き戻す")
    parser.add_argument("task_id", help="GitLab Issue IID")
    parser.add_argument("vault_path", help="Obsidian vault のパス")
    parser.add_argument("--note", help="ノートファイルのパス（省略時は vault 内を task_id で検索）")
    args = parser.parse_args()
    write_back_to_obsidian(args.task_id, args.vault_path, args.note)
