#!/usr/bin/env python3
"""取り込んだスキルを Windows / Copilot 環境向けに適応する。

使い方:
    python adapt_for_windows.py <skill-dir>

処理内容:
    1. SKILL.md 内の `python3` → `python` 置換
    2. SKILL.md 内のコードブロック中の `~/.copilot/` → `%USERPROFILE%\\.copilot\\` 置換
    3. .py ファイルの shebang (#!/usr/bin/env python3) を python に変更
    4. .sh ファイルが存在する場合は警告を出力

出力 (エージェントが解析する機械可読行):
    ADAPT_SKILL_MD: ok|skip
    ADAPT_PYTHON_FILES: ok|skip  N件
    ADAPT_SHELL_WARNING: ok|warn  [ファイルパス...]
    ADAPT_RESULT: ok|warn
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SHEBANG_PYTHON3 = "#!/usr/bin/env python3"
SHEBANG_PYTHON = "#!/usr/bin/env python"


# ---------------------------------------------------------------------------
# SKILL.md 書き換え
# ---------------------------------------------------------------------------

def adapt_skill_md(skill_dir: str) -> str:
    """SKILL.md を Windows 向けに書き換える。戻り値: 'ok' | 'skip'"""
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md_path):
        return "skip"

    with open(md_path, encoding="utf-8") as f:
        original = f.read()

    patched = original

    # python3 → python（コードブロック内外を問わず置換）
    patched = re.sub(r'\bpython3\b', 'python', patched)

    # ~/.copilot/ → %USERPROFILE%\.copilot\（コードブロック内のみ対象）
    patched = _replace_in_code_blocks(
        patched,
        r'~/\.copilot/',
        r'%USERPROFILE%\\.copilot\\',
    )

    if patched == original:
        return "skip"

    with open(md_path, encoding="utf-8", newline="") as f:
        pass  # エンコーディング確認のみ
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(patched)
    return "ok"


def _replace_in_code_blocks(text: str, pattern: str, replacement: str) -> str:
    """fenced コードブロック（``` ... ```）内のパターンだけ置換する。"""
    result = []
    compiled = re.compile(pattern)
    # コードブロックを ``` で分割し、奇数インデックスがコードブロック内
    parts = re.split(r'(```[^\n]*\n[\s\S]*?```)', text)
    for i, part in enumerate(parts):
        if i % 2 == 1:  # コードブロック内
            result.append(compiled.sub(replacement, part))
        else:
            result.append(part)
    return "".join(result)


# ---------------------------------------------------------------------------
# Python shebang 書き換え
# ---------------------------------------------------------------------------

def adapt_python_files(skill_dir: str) -> tuple[str, int]:
    """Python ファイルの shebang を書き換える。戻り値: ('ok'|'skip', 変更件数)"""
    changed = 0
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            if not content.startswith(SHEBANG_PYTHON3):
                continue

            patched = SHEBANG_PYTHON + content[len(SHEBANG_PYTHON3):]
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(patched)
            changed += 1

    return ("ok" if changed > 0 else "skip", changed)


# ---------------------------------------------------------------------------
# シェルスクリプト確認
# ---------------------------------------------------------------------------

def check_shell_scripts(skill_dir: str) -> tuple[str, list[str]]:
    """(status, [相対パス...]) を返す。status: ok / warn"""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            if fname.endswith((".sh", ".bash")):
                rel = os.path.relpath(os.path.join(dirpath, fname), skill_dir)
                found.append(rel)
    return ("warn", found) if found else ("ok", [])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="インストール済みスキルを Windows / Copilot 環境向けに適応する",
    )
    parser.add_argument("skill_dir", help="インストール済みスキルのディレクトリパス")
    args = parser.parse_args()

    skill_dir = os.path.expandvars(os.path.expanduser(args.skill_dir))

    if not os.path.isdir(skill_dir):
        print(f"エラー: ディレクトリが見つかりません: {skill_dir}")
        sys.exit(1)

    md_status = adapt_skill_md(skill_dir)
    print(f"ADAPT_SKILL_MD: {md_status}")

    py_status, py_count = adapt_python_files(skill_dir)
    print(f"ADAPT_PYTHON_FILES: {py_status}  {py_count}件")

    sh_status, sh_files = check_shell_scripts(skill_dir)
    print(f"ADAPT_SHELL_WARNING: {sh_status}")
    for f in sh_files:
        print(f"  🐚  {f}")

    overall = "warn" if sh_status == "warn" else "ok"
    print(f"ADAPT_RESULT: {overall}")


if __name__ == "__main__":
    main()
