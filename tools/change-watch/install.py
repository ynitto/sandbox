#!/usr/bin/env python3
"""change-watch のインストーラ。本体とプラグインを 1 つの実行可能な zip（Python の zipapp、.pyz）にまとめて置く。

    python install.py build   [--out dist/change-watch.pyz] [--python PY] [--no-yaml]
    python install.py install [--dir DIR] [--python PY] [--no-yaml]
    python install.py uninstall [--dir DIR]

- zip に入れるもの: 本体（changewatch/。同梱のプラグイン changewatch/plugins/ を含む）・入口（__main__.py）、
  それに PyYAML があればその純 Python の部分（YAML の設定を読むため。--no-yaml で入れない）
- install の置き場所（--dir の既定）:
    Linux / WSL / macOS  ~/.local/bin/change-watch        先頭に #! を付けた実行ファイル
    Windows              %LOCALAPPDATA%\\Programs\\change-watch\\change-watch.pyz と change-watch.cmd
- 実行に使う Python は --python（既定: Linux などは /usr/bin/env python3、Windows はこのインストーラを動かした Python）

使い方（入れたあと）:

    change-watch --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml run
    change-watch --plugin dagu --connections ... fetch --source <取得元>   （Dagu のワークフローの例は dags/*.example.yaml）

標準ライブラリだけで動く。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import zipapp
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "change-watch"
IS_WINDOWS = os.name == "nt"

MAIN = '''"""change-watch（zipapp の入口）。"""
import sys

from changewatch.cli import main

sys.exit(main())
'''


def _copy_py(src: Path, dst: Path) -> int:
    """src の下の .py だけを dst へ写す（テスト・__pycache__・拡張モジュールは入れない）。"""
    n = 0
    for f in sorted(src.rglob("*.py")):
        rel = f.relative_to(src)
        if "__pycache__" in rel.parts or any(p in ("test", "tests") for p in rel.parts):
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        n += 1
    return n


def _yaml_dir() -> Path | None:
    spec = importlib.util.find_spec("yaml")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _yaml_license() -> str | None:
    try:
        from importlib import metadata
        for f in metadata.files("PyYAML") or []:
            if f.name.upper().startswith("LICENSE"):
                return Path(f.locate()).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001  ライセンス文が見つからなくても作れるようにする
        return None
    return None


def build(out: Path, interpreter: str | None, with_yaml: bool) -> Path:
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="change-watch-build-") as tmp:
        stage = Path(tmp) / "app"
        stage.mkdir()
        n = _copy_py(HERE / "changewatch", stage / "changewatch")
        (stage / "__main__.py").write_text(MAIN, encoding="utf-8")
        yaml_note = "入れていません"
        if with_yaml:
            ydir = _yaml_dir()
            if ydir is None:
                yaml_note = "この Python に PyYAML が無いので入れていません（設定は JSON で書くか、実行する Python に PyYAML を入れる）"
            else:
                # 純 Python の部分だけ（C 拡張の _yaml は zip から読み込めないが、無ければ PyYAML は純 Python で動く）
                _copy_py(ydir, stage / "yaml")
                lic = _yaml_license()
                if lic:
                    (stage / "yaml" / "LICENSE").write_text(lic, encoding="utf-8")
                import yaml
                yaml_note = f"PyYAML {yaml.__version__} を同梱しました"
        if out.exists():
            out.unlink()
        zipapp.create_archive(stage, out, interpreter=interpreter, compressed=True)
    print(f"作りました: {out}（{n} ファイル、{out.stat().st_size // 1024} KB）。YAML: {yaml_note}")
    return out


def default_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / NAME
    return Path.home() / ".local" / "bin"


def install(dest: Path, python: str | None, with_yaml: bool) -> list[Path]:
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    if IS_WINDOWS:
        pyz = build(dest / f"{NAME}.pyz", None, with_yaml)
        py = python or sys.executable
        cmd = dest / f"{NAME}.cmd"
        cmd.write_text(f'@"{py}" "%~dp0{NAME}.pyz" %*\r\n', encoding="utf-8")
        written += [pyz, cmd]
    else:
        exe = build(dest / NAME, python or "/usr/bin/env python3", with_yaml)
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(exe)
    for w in written:
        print(f"置きました: {w}")
    path_dirs = [Path(p).resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if dest not in path_dirs:
        print(f"注意: {dest} が PATH にありません。PATH に足すか、フルパスで実行してください")
    return written


def uninstall(dest: Path) -> None:
    dest = dest.expanduser().resolve()
    names = [f"{NAME}.pyz", f"{NAME}.cmd"] if IS_WINDOWS else [NAME]
    for n in names:
        p = dest / n
        if p.exists():
            p.unlink()
            print(f"消しました: {p}")
    print("状態（--state-dir / 設定の state_dir）と設定ファイルは残しています")


def main(argv: list[str] | None = None) -> int:
    # 表示は UTF-8 で出す（Windows でパイプにつないだときなど、cp1252 では日本語を出せずに落ちる）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="change-watch を 1 つの実行可能な zip にまとめて置く")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="zip（.pyz）を作るだけ")
    b.add_argument("--out", default=str(HERE / "dist" / f"{NAME}.pyz"))
    b.add_argument("--python", help="先頭に書く #! のインタプリタ（例: /usr/bin/env python3）")
    b.add_argument("--no-yaml", action="store_true", help="PyYAML を同梱しない")
    i = sub.add_parser("install", help="作って置く")
    i.add_argument("--dir", default=str(default_dir()), help=f"置き場所（既定: {default_dir()}）")
    i.add_argument("--python", help="実行に使う Python")
    i.add_argument("--no-yaml", action="store_true", help="PyYAML を同梱しない")
    u = sub.add_parser("uninstall", help="置いたものを消す")
    u.add_argument("--dir", default=str(default_dir()))
    a = p.parse_args(argv)
    if a.cmd == "build":
        build(Path(a.out), a.python, not a.no_yaml)
    elif a.cmd == "install":
        install(Path(a.dir), a.python, not a.no_yaml)
    else:
        uninstall(Path(a.dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
