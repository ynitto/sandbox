#!/usr/bin/env python3
"""
classify_task.py — Obsidian タスクノートを GitLab Issue として登録し、
フロントマターに task_id / category / priority を書き戻す。

使い方:
  python classify_task.py <note_path>

環境変数:
  GITLAB_TOKEN   GitLab パーソナルアクセストークン（必須）
  GL_SCRIPT      gl.py のパス（省略時: 自動検索）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# フロントマター解析・更新
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
    updated_keys = set()
    new_lines = []
    for line in lines:
        if ":" not in line:
            new_lines.append(line)
            continue
        k = line.split(":")[0].strip()
        if k in updates:
            v = updates[k]
            new_lines.append(f"{k}: {v}")
            updated_keys.add(k)
        else:
            new_lines.append(line)
    # 未存在キーを末尾に追加
    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}: {v}")
    return f"---\n{chr(10).join(new_lines)}\n---\n" + text[m.end():]


# ---------------------------------------------------------------------------
# ノート本文からタイトル・本文を抽出
# ---------------------------------------------------------------------------

def _extract_body(text: str) -> tuple[str, str]:
    """(タスク概要, 受け入れ条件) を返す。"""
    body_m = re.search(r"## タスク概要\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    ac_m   = re.search(r"## 受け入れ条件\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    body = body_m.group(1).strip() if body_m else ""
    ac   = ac_m.group(1).strip()   if ac_m   else ""
    return body, ac


# ---------------------------------------------------------------------------
# LLM なしの簡易分類（キーワードマッチ）
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    "code_review":  ["レビュー", "review", "コードチェック"],
    "refactoring":  ["リファクタ", "refactor", "整理", "シンプル"],
    "testing":      ["テスト", "test", "カバレッジ", "coverage"],
    "deployment":   ["デプロイ", "deploy", "リリース", "release", "ビルド"],
    "research":     ["調査", "research", "調べ", "リサーチ"],
}

_PRIORITY_KEYWORDS = {
    1: ["緊急", "urgent", "critical", "即時"],
    2: ["高", "high", "重要"],
    4: ["低", "low", "後回し"],
}


def _classify(title: str, body: str) -> tuple[str, int]:
    combined = (title + " " + body).lower()
    category = "other"
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw.lower() in combined for kw in kws):
            category = cat
            break
    priority = 3  # デフォルト: normal
    for pri, kws in _PRIORITY_KEYWORDS.items():
        if any(kw.lower() in combined for kw in kws):
            priority = pri
            break
    return category, priority


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
# メイン
# ---------------------------------------------------------------------------

def classify_and_enqueue(note_path: str) -> str:
    path = Path(note_path)
    if not path.exists():
        sys.exit(f"ERROR: ファイルが見つかりません: {note_path}")

    text = path.read_text(encoding="utf-8")
    fm = _parse_fm(text)

    # 既に登録済みなら task_id を返す
    if fm.get("task_id"):
        print(f"[classify] 既に登録済み: task_id={fm['task_id']}")
        return fm["task_id"]

    title = fm.get("title") or path.stem
    body, ac = _extract_body(text)
    category, priority = _classify(title, body)

    # フロントマターの priority が設定済みなら優先
    if fm.get("priority"):
        try:
            priority = int(fm["priority"])
        except ValueError:
            pass

    gl_script = _find_gl_script()

    # Issue 本文を一時ファイルに書き出す
    priority_label = {1: "high", 2: "high", 3: "normal", 4: "low", 5: "low"}.get(priority, "normal")
    issue_body = f"""## 目的

{body or title}

## 受け入れ条件

{ac or "- [ ] タスクを完了する"}

## 技術制約

特になし

## ソース

Obsidian ノート: {path.name}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(issue_body)
        body_file = f.name

    try:
        raw = _gl(
            gl_script,
            "create-issue",
            "--title", title,
            "--body-file", body_file,
            "--labels", f"status:open,assignee:any,priority:{priority_label},ai-task",
        )
    finally:
        os.unlink(body_file)

    issue = json.loads(raw)
    task_id = str(issue["iid"])

    # フロントマターを更新
    updated = _update_fm(text, {
        "task_id": task_id,
        "category": category,
        "priority": str(priority),
        "status": "In Progress",
    })
    path.write_text(updated, encoding="utf-8")

    print(f"[classify] GitLab Issue #{task_id} を作成しました。category={category}, priority={priority}")
    return task_id


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"使い方: {sys.argv[0]} <note_path>")
        sys.exit(1)
    classify_and_enqueue(sys.argv[1])
