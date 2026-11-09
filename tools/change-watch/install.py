#!/usr/bin/env python3
"""change-watch のインストーラ。本体とプラグインを 1 つの実行可能な zip（Python の zipapp、.pyz）にまとめて置き、
agents/ の下のエージェント（change-watch の上で動くもの）も同じ場所の決まりで入れる。

    python install.py build   [--out dist/change-watch.pyz] [--python PY] [--no-yaml]
    python install.py install [--dir DIR] [--python PY] [--no-yaml] [--agent NAME ...] [--dagu-dir DIR] [--no-dags]
    python install.py uninstall [--dir DIR] [--agent NAME ...]
    python install.py agents                     入れられるエージェントと、入っているか

- zip に入れるもの: 本体（changewatch/。同梱のプラグイン changewatch/plugins/ を含む）・入口（__main__.py）、
  それに PyYAML があればその純 Python の部分（YAML の設定を読むため。--no-yaml で入れない）
- install の置き場所（--dir の既定）:
    Linux / WSL / macOS  ~/.local/bin/change-watch        先頭に #! を付けた実行ファイル
    Windows              %LOCALAPPDATA%\\Programs\\change-watch\\change-watch.pyz と change-watch.cmd
- 実行に使う Python は --python（既定: Linux などは /usr/bin/env python3、Windows はこのインストーラを動かした Python）
- --agent NAME（agents/NAME。繰り返し可）で、エージェントも入れる。置き場所はどのエージェントも同じ決まり:
    プログラム（agents/NAME/*.py。入れ直すと上書き＝更新）
        Linux など  ~/.local/share/change-watch/agents/NAME/
        Windows     %LOCALAPPDATA%\\Programs\\change-watch\\agents\\NAME\\（change-watch.pyz の隣）
    設定の雛形（agents/NAME/*.example のうちワークフロー以外。.example を外す）  ~/.config/change-watch/NAME/
    Dagu のワークフロー（agents/NAME/change-watch-*.yaml.example）               ~/.config/dagu/dags/（--dagu-dir で変える）
    状態（設定の state_dir）                                                    ~/.local/state/change-watch/NAME/
  設定の雛形とワークフローは、無いときだけ置く（直した設定を上書きしない）。例の /home/you の下のパスを、この PC の
  change-watch・プログラム・設定・接続設定（~/.github/connections.yaml）の場所に置き換える。Windows では、ワークフローの
  command を Python に、args の先頭を change-watch.pyz にし、設定のコマンドの python3 を Python のフルパスにする。
  置き換えずに残った /home/you（作業フォルダなど）は、直すところとして表示する

使い方（入れたあと）:

    change-watch --config ~/.config/change-watch/change-watch.yaml --connections ~/.github/connections.yaml get --source <取得元>
    （Dagu のワークフローの例は dags/*.example.yaml。条件をワークフローで判定する形は dags/change-watch-get.example.yaml）

標準ライブラリだけで動く。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import stat
import sys
import tempfile
import zipapp
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "change-watch"
AGENTS = HERE / "agents"
PLACEHOLDER = "/home/you"
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


# ---------------------------------------------------------------------------
# エージェント（agents/NAME）
# ---------------------------------------------------------------------------

def available_agents() -> list[str]:
    """agents/ の下で、プログラム（.py）を持つフォルダの名前。"""
    if not AGENTS.is_dir():
        return []
    return sorted(d.name for d in AGENTS.iterdir() if d.is_dir() and any(d.glob("*.py")))


def agents_home(windows: bool, home: Path, bin_dir: Path) -> Path:
    """エージェントのプログラムの置き場所（どのエージェントもこの下の NAME/）。"""
    return bin_dir / "agents" if windows else home / ".local" / "share" / NAME / "agents"


def _posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def render(text: str, *, windows: bool, home: Path, bin_dir: Path, python: str, is_dag: bool) -> str:
    """例の /home/you の下のパスを、この PC の置き場所にする（agents/NAME の *.example 用）。"""
    agents = agents_home(windows, home, bin_dir)
    text = text.replace(f"{PLACEHOLDER}/.local/share/{NAME}/agents", _posix(agents))
    text = text.replace(f"{PLACEHOLDER}/.config/{NAME}", _posix(home / ".config" / NAME))
    text = text.replace(f"{PLACEHOLDER}/.github/connections.yaml", _posix(home / ".github" / "connections.yaml"))
    exe = f"{PLACEHOLDER}/.local/bin/{NAME}"
    if windows:
        py = _posix(python)
        pyz = _posix(bin_dir / f"{NAME}.pyz")
        if is_dag:
            # command: change-watch → command: python、args の先頭に change-watch.pyz
            text = re.sub(r'(\n\s*command: )"' + re.escape(exe) + r'"(\n\s*args:\n)(\s*)- ',
                          lambda m: f'{m.group(1)}"{py}"{m.group(2)}{m.group(3)}- "{pyz}"\n{m.group(3)}- ', text)
        text = re.sub(r"(?m)^(\s*- )python3$", lambda m: m.group(1) + py, text)     # 設定のコマンドの argv
        text = re.sub(r'(?m)^(\s*(?:command: |- ))"python3"', lambda m: f'{m.group(1)}"{py}"', text)   # ワークフローの Python
        text = text.replace("=python3 ", f"={py} ")                                 # .env のコマンド
    else:
        text = text.replace(exe, _posix(bin_dir / NAME))
    return text


def agent_files(agent: str) -> tuple[list[Path], list[Path], list[Path]]:
    """(プログラム, 設定の雛形, ワークフロー)。どれもエージェントのフォルダの直下だけを見る。"""
    d = AGENTS / agent
    programs = sorted(d.glob("*.py"))
    examples = sorted(p for p in d.glob("*.example") if p.is_file())
    dags = [p for p in examples if p.name.startswith(f"{NAME}-") and p.name.endswith(".yaml.example")]
    return programs, [p for p in examples if p not in dags], dags


def install_agent(agent: str, *, windows: bool, home: Path, bin_dir: Path, python: str,
                  dagu_dir: Path | None) -> list[str]:
    """エージェントを 1 つ入れる。直すところ（残った /home/you）の一覧を返す。"""
    programs, templates, dags = agent_files(agent)
    prog_dir = agents_home(windows, home, bin_dir) / agent
    prog_dir.mkdir(parents=True, exist_ok=True)
    for f in programs:
        shutil.copy2(f, prog_dir / f.name)
        print(f"置きました: {prog_dir / f.name}")
    todo: list[str] = []
    targets = [(f, home / ".config" / NAME / agent, False) for f in templates]
    if dagu_dir is not None:
        targets += [(f, dagu_dir / "dags", True) for f in dags]
    for f, dest_dir, is_dag in targets:
        out = dest_dir / f.name[: -len(".example")]
        if out.exists():
            print(f"既にあるので触りません: {out}（新しい雛形: {f}）")
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        text = render(f.read_text(encoding="utf-8"), windows=windows, home=home, bin_dir=bin_dir, python=python,
                      is_dag=is_dag)
        out.write_text(text, encoding="utf-8")
        print(f"置きました: {out}")
        for n, line in enumerate(text.splitlines(), 1):
            if PLACEHOLDER in line and not line.lstrip().startswith("#"):
                todo.append(f"{out}:{n}: {line.strip()}")
    old = home / ".local" / "share" / agent
    if old.is_dir():
        print(f"注意: 前の版の置き場所 {old} は使わなくなりました。ワークフローを新しいものにしたら消してください")
    old_state = home / ".local" / "state" / NAME / "state.db"
    if old_state.exists() and not (home / ".local" / "state" / NAME / agent / "state.db").exists():
        print(f"注意: 前の版の状態 {old_state} があります。処理し終えたものを引き継ぐなら、動かす前に "
              f"{old_state.parent}/state.db* を {old_state.parent / agent}/ へ移してください（移さないと最初から数え直します）")
    return todo


def uninstall_agent(agent: str, *, windows: bool, home: Path, bin_dir: Path) -> None:
    d = agents_home(windows, home, bin_dir) / agent
    if d.is_dir():
        shutil.rmtree(d)
        print(f"消しました: {d}")
    print(f"{agent} の設定（{home / '.config' / NAME / agent}）・状態・Dagu のワークフローは残しています")


def _check_agents(names: list[str] | None) -> list[str]:
    names = names or []
    known = available_agents()
    bad = [n for n in names if n not in known]
    if bad:
        raise SystemExit(f"エージェント {', '.join(bad)} はありません（あるもの: {', '.join(known) or 'なし'}）")
    return names


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
    i.add_argument("--agent", action="append", metavar="NAME",
                   help=f"エージェントも入れる（繰り返し可。あるもの: {', '.join(available_agents()) or 'なし'}）")
    i.add_argument("--dagu-dir", default=str(Path.home() / ".config" / "dagu"),
                   help="Dagu の設定のフォルダ。ワークフローはこの下の dags/ に置く（既定: ~/.config/dagu）")
    i.add_argument("--no-dags", action="store_true", help="Dagu のワークフローを置かない")
    u = sub.add_parser("uninstall", help="置いたものを消す（--agent なら、そのエージェントのプログラムだけ）")
    u.add_argument("--dir", default=str(default_dir()))
    u.add_argument("--agent", action="append", metavar="NAME")
    sub.add_parser("agents", help="入れられるエージェントと、入っているか")
    a = p.parse_args(argv)
    home = Path.home()
    if a.cmd == "build":
        build(Path(a.out), a.python, not a.no_yaml)
    elif a.cmd == "install":
        agents = _check_agents(a.agent)
        bin_dir = Path(a.dir).expanduser().resolve()
        install(bin_dir, a.python, not a.no_yaml)
        todo: list[str] = []
        for agent in agents:
            todo += install_agent(agent, windows=IS_WINDOWS, home=home, bin_dir=bin_dir,
                                  python=a.python or sys.executable,
                                  dagu_dir=None if a.no_dags else Path(a.dagu_dir).expanduser())
        if todo:
            print("自分のものに直すところ（/home/you のまま）:")
            for t in todo:
                print(f"  {t}")
    elif a.cmd == "agents":
        bin_dir = default_dir()
        for agent in available_agents():
            here = (agents_home(IS_WINDOWS, home, bin_dir) / agent).is_dir()
            print(f"{agent}\t{'入っています' if here else '入っていません'}")
    else:
        agents = _check_agents(a.agent)
        bin_dir = Path(a.dir).expanduser().resolve()
        if agents:
            for agent in agents:
                uninstall_agent(agent, windows=IS_WINDOWS, home=home, bin_dir=bin_dir)
        else:
            uninstall(bin_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
