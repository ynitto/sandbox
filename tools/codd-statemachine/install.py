#!/usr/bin/env python3
"""codd-statemachine をリポジトリへ置く（置き先の名前は `.statemachine/codd/`）。

    python3 tools/codd-statemachine/install.py <実装のリポジトリ> --side impl --ref docs=../my-design
    python3 tools/codd-statemachine/install.py <設計書のリポジトリ> --side design --ref ../my-impl
    python3 tools/codd-statemachine/install.py <実装のリポジトリ> --side impl --ref api=../api-docs --ref ui=../ui-docs

    # 実装と設計書が同じリポジトリ（src/ と docs/）にあるとき
    python3 tools/codd-statemachine/install.py <リポジトリ> --side impl --scope src --scope tests \\
        --ref docs=. --ref-scope docs=docs

--ref は `名前=パス` か `パス`（名前はフォルダ名）。いくつでも渡せる。
--scope は自分が受け持つフォルダ、--ref-scope は `参照先の名前=フォルダ` で参照先が受け持つフォルダ（どちらも繰り返し可）。
同じリポジトリを参照先にするときは両方が要る。

`<リポジトリ>/.statemachine/codd/` に machine/ の中身を写し、codd.json を書く。
既に置いてあれば定義とスクリプトを入れ替え（古いファイルは消す）、codd.json は --side / --ref を渡したときだけ書き換える
（--ref を渡すと参照先の一覧を丸ごと入れ替える）。使うスキルは codd.json の skills を手で書く。
`.codd/`（計画・探した結果・graphify のグラフ）は .gitignore に足す（--no-gitignore で足さない）。
このマシン自身が graphify の索引に入らないよう、.graphifyignore に `.statemachine/codd/` を足す。
置いたあと、自分と参照先から決まりらしいマークダウン（コーディングルールなど）を探して codd.json の rules /
refs[].rules に書く（--no-discover-rules でやめる。あとからは `codd.py rules --write`）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "machine"
DEST_REL = Path(".statemachine") / "codd"
IGNORE_LINE = ".codd/"
# graphify で知識グラフを作るとき、このマシン自身を索引に入れない。
GRAPHIFY_IGNORE_LINE = ".statemachine/codd/"


def parse_ref(value: str) -> dict:
    """`名前=パス` か `パス` を refs の 1 項目にする。"""
    name, sep, path = value.partition("=")
    return {"name": name, "path": path} if sep and name and path else {"path": value}


def ref_name(ref: dict) -> str:
    return ref.get("name") or Path(str(ref["path"]).rstrip("/\\")).name


def install(target: Path, side: str | None, refs: list[str] | None, gitignore: bool = True,
            scope: list[str] | None = None, ref_scopes: list[str] | None = None, discover: bool = True) -> Path:
    if not (target / ".git").exists():
        raise SystemExit(f"git リポジトリではありません: {target}")
    dest = target / DEST_REL
    config_file = dest / "codd.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else None
    if config is None and (side is None or not refs):
        raise SystemExit("初めて置くときは --side と --ref を指定してください")

    dest.mkdir(parents=True, exist_ok=True)
    # 古い版のファイルを残さない（codd.json だけは利用者の設定なので残す）。
    for item in dest.iterdir():
        if item.name == "codd.json":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in SRC.iterdir():
        if item.name in ("codd.json", "__pycache__"):
            continue
        target_item = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target_item, dirs_exist_ok=True)
        else:
            shutil.copyfile(item, target_item)

    config = config or {}
    if side:
        config["side"] = side
    if refs:
        config.pop("ref_path", None)  # 参照先が 1 つだった頃の書き方は refs に置き換える
        config["refs"] = [parse_ref(r) for r in refs]
    if scope:
        config["scope"] = list(scope)
    for value in ref_scopes or []:
        name, sep, folder = value.partition("=")
        entry = next((r for r in config.get("refs", []) if ref_name(r) == name), None)
        if not (sep and folder) or entry is None:
            raise SystemExit(f"--ref-scope は `参照先の名前=フォルダ` です（今: {value!r}。参照先: "
                             + ", ".join(ref_name(r) for r in config.get("refs", [])) + "）")
        if not entry.get("name"):
            entry["name"] = name
        entry.setdefault("scope", [])
        if folder not in entry["scope"]:
            entry["scope"].append(folder)
    config.setdefault("skills", {"plan": [], "apply": []})
    config.setdefault("graphify", "auto")
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if discover:
        discover_rules(target, dest)
    if gitignore:
        append_line(target / ".gitignore", IGNORE_LINE)
    append_line(target / ".graphifyignore", GRAPHIFY_IGNORE_LINE)
    return dest


def discover_rules(target: Path, dest: Path) -> None:
    """自分と参照先から決まりらしいマークダウン（コーディングルールなど）を探し、codd.json の rules に書く。"""
    proc = subprocess.run([sys.executable, str(dest / "codd.py"), "rules", "--write"], cwd=target,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        reason = (proc.stderr.strip().splitlines() or [""])[-1]
        print("  決まりの候補は探せませんでした（参照先を置いてから `python3 .statemachine/codd/codd.py rules --write`）: "
              + reason, file=sys.stderr)
        return
    if "件を" in proc.stdout:
        print("  決まりの候補を codd.json の rules に書きました（決まりでないものは手で消してください）:")
        start = proc.stdout.find("候補:")
        print("\n".join("  " + ln for ln in proc.stdout[start:].splitlines()[1:] if ln.startswith("  - ")))


def append_line(path: Path, line: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if line in lines:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    prefix = "" if not text or text.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}{line}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="codd-statemachine をリポジトリへ置く")
    p.add_argument("target", help="置き先のリポジトリ")
    p.add_argument("--side", choices=["impl", "design"], help="このリポジトリの側（impl = 実装 / design = 設計書）")
    p.add_argument("--ref", action="append",
                   help="参照先。`名前=パス` か `パス`（置き先からの相対でも絶対でもよい）。繰り返し可")
    p.add_argument("--scope", action="append", help="自分が受け持つフォルダ（繰り返し可。既定はリポジトリ全体）")
    p.add_argument("--ref-scope", action="append",
                   help="`参照先の名前=フォルダ`。その参照先が受け持つフォルダ（繰り返し可）")
    p.add_argument("--no-discover-rules", action="store_true",
                   help="決まりらしいマークダウン（コーディングルールなど）を探して codd.json の rules に書くのをやめる")
    p.add_argument("--no-gitignore", action="store_true", help=".gitignore に .codd/ を足さない")
    args = p.parse_args(argv)
    dest = install(Path(args.target).resolve(), args.side, args.ref, gitignore=not args.no_gitignore,
                   scope=args.scope, ref_scopes=args.ref_scope, discover=not args.no_discover_rules)
    config = json.loads((dest / "codd.json").read_text(encoding="utf-8"))
    print(f"置きました: {dest}")
    refs = config.get("refs") or [{"path": config.get("ref_path")}]
    print(f"  この側: {config['side']}  参照先: " + ", ".join(
        f"{ref_name(r)}={r['path']}" + (f"（{', '.join(r['scope'])}）" if r.get("scope") else "") for r in refs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
