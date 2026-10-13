#!/usr/bin/env python3
"""Windows と喋るファイルの subprocess が、出力の文字コードを決め打ちしていないことを確かめる。

本文に `cmd.exe` / `wslpath` / `powershell` のいずれかを含む `.py`（＝ Windows のプロセスと
喋ることを自分で宣言しているファイル）だけを対象に、`subprocess.run` / `Popen` /
`check_output` / `call` / `check_call` の呼び出しで `text=True` または
`universal_newlines=True` を渡しながら `encoding=` を渡していないものを ERROR にする。
WSL（UTF-8）の Python が cmd.exe（CP932）を呼ぶと、ロケールの既定で読んで
`UnicodeDecodeError` になるため。直し方は `text=True` を外して bytes で受け、
`decode_cli_output()`（UTF-8 → だめなら CP932）に通すこと。`encoding="cp932"` の直書きは
WSL 側の UTF-8 出力を読み違えるので直し方にならない。

grep ではなく ast で見る。呼び出しの関数名だけで突き合わせると、同名の自前関数や
別モジュールの `run()` まで数えてしまう。`subprocess.<名前>` と
`from subprocess import <名前>` で取り込んだ名前の呼び出しだけを見る。
テスト（`tests/` `test/` 配下と `test_*.py` `*_test.py`）は Linux の CI でしか動かないので対象外、
Windows と喋らないファイルの既存の無指定（評価ハーネスなど）も対象外。

使い方:

    python3 tools/ci/check_subprocess_encoding.py            # リポジトリ全体（CI と同じ）
    python3 tools/ci/check_subprocess_encoding.py --list     # 検査対象のファイル一覧
    python3 tools/ci/check_subprocess_encoding.py <file>...  # 指定ファイルだけ

終了コード: 0 = 違反なし / 1 = 違反あり / 2 = 使い方の誤り。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Windows と喋ることの目印。**増やすならここだけ**。
WINDOWS_MARKERS = re.compile(r"cmd\.exe|wslpath|powershell", re.IGNORECASE)

SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "call", "check_call"})
TEXT_KEYWORDS = ("text", "universal_newlines")

HELPER = "decode_cli_output()"


def talks_to_windows(source: str) -> bool:
    return bool(WINDOWS_MARKERS.search(source))


def is_test_file(path: Path) -> bool:
    name = path.name
    return (bool({"tests", "test"} & set(path.parts[:-1]))
            or name.startswith("test_") or name.endswith("_test.py"))


def _subprocess_names(tree: ast.AST) -> "tuple[set[str], set[str]]":
    """(subprocess モジュールの別名, subprocess から取り込んだ関数名) を返す。"""
    modules: "set[str]" = set()
    funcs: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    funcs.add(alias.asname or alias.name)
    return modules, funcs


def _is_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def check_source(source: str, filename: str = "<string>") -> "list[tuple[int, str]]":
    """違反を (行番号, 関数名) で返す。Windows と喋らないファイルは常に空。"""
    if not talks_to_windows(source):
        return []
    with warnings.catch_warnings():          # docstring 内の \ などの SyntaxWarning は検査に無関係
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source, filename=filename)
    modules, funcs = _subprocess_names(tree)
    found: "list[tuple[int, str]]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_FUNCS
                and isinstance(func.value, ast.Name) and func.value.id in modules):
            name = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in funcs:
            name = func.id
        else:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
        if "encoding" in kw:
            continue
        if any(k in kw and _is_true(kw[k]) for k in TEXT_KEYWORDS):
            found.append((node.lineno, name))
    return found


def target_files() -> "list[Path]":
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    files = []
    for rel in out.splitlines():
        if is_test_file(Path(rel)):
            continue
        path = ROOT / rel
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if talks_to_windows(source):
            files.append(path)
    return files


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--list", action="store_true", help="検査対象のファイル一覧を出す")
    args = parser.parse_args(argv)

    files = [p.resolve() for p in args.files] if args.files else target_files()
    if args.list:
        for path in files:
            print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
        return 0

    errors = 0
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"ERROR: {path}: 読めない: {e}", file=sys.stderr)
            return 2
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        for lineno, name in check_source(source, str(path)):
            errors += 1
            print(f"ERROR: {rel}:{lineno}: {name}(..., text=True) に encoding= が無い。"
                  f"text=True を外して bytes で受け、{HELPER} に通すこと"
                  f"（encoding=\"cp932\" の直書きは WSL の UTF-8 出力を読み違える）")
    if errors:
        print(f"\n{errors} 件。Windows（cmd.exe / wslpath / PowerShell）と喋るファイルの"
              f" subprocess 出力は {HELPER} で読む。", file=sys.stderr)
        return 1
    print(f"OK: Windows と喋る {len(files)} ファイルの subprocess に文字コードの決め打ちなし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
