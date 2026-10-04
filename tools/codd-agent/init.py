#!/usr/bin/env python3
"""リポジトリに codd-agent を置いて初期設定する（置き先の名前は `.statemachine/codd/`）。

リポジトリごとに 1 回（と、新しい版を置き直すとき）実行する。graphify などの外部のミドルウェアを端末に入れるのは
`install.py` の仕事（端末ごとに 1 回）。

    python3 tools/codd-agent/init.py <実装のリポジトリ> --side impl --ref docs=../my-design
    python3 tools/codd-agent/init.py <設計書のリポジトリ> --side design --ref ../my-impl
    python3 tools/codd-agent/init.py <実装のリポジトリ> --side impl --ref api=../api-docs --ref ui=../ui-docs

    # 実装と設計書が同じリポジトリ（src/ と docs/）にあるとき
    python3 tools/codd-agent/init.py <リポジトリ> --side impl --scope src --scope tests \\
        --ref docs=. --ref-scope docs=docs

--ref は `名前=パス` か `パス`（名前はフォルダ名）。いくつでも渡せる。
--scope は自分が受け持つフォルダ、--ref-scope は `参照先の名前=フォルダ` で参照先が受け持つフォルダ（どちらも繰り返し可）。
同じリポジトリを参照先にするときは両方が要る。
--exclude は自分の除外パターン、--ref-exclude は `参照先の名前=除外パターン`（どちらも繰り返し可）。
渡した側の一覧を入れ替え、--exclude "" / --ref-exclude 名前= で空にする。

`<リポジトリ>/.statemachine/codd/` に machine/ の中身を写し、codd.json を書く。
既に置いてあれば定義とスクリプトを入れ替え（古いファイルは消す）、codd.json は上書きしない。--side / --ref / --scope /
--ref-scope / --exclude / --ref-exclude / --check / --test / --evidence を渡したときだけ、その項目を書き換える（--ref は参照先の一覧を入れ替えるが、同じ名前の
参照先に手で書いた rules・scope・exclude などは残す）。使うスキルは codd.json の skills を手で書く。
`.codd/`（探した結果・検査の記録・変える前の写し・graphify のグラフ）は .gitignore に足す（--no-gitignore で足さない）。
このマシン自身が graphify の索引に入らないよう、.graphifyignore に `.statemachine/codd/` を足す。
初めて置いたときは、自分と参照先から決まりらしいマークダウン（コーディングルールなど）を探して codd.json の rules /
refs[].rules に書く（--no-discover-rules でやめる。あとからは `codd.py rules --write`）。
kiro-cli と GitHub Copilot 向けに、必ずこのマシンで変えるカスタムエージェント `codd` を書く
（`.kiro/agents/codd.json` と `.github/agents/codd.agent.md`。--agent で絞り、--no-agents で書かない）。
--check "コマンド" で、変えたあとに実行する検査コマンド（codd.json の check）を書く。
--evidence "パス" で結果ファイルの一覧を指定する（繰り返し可。"" で扱わない）。検査ツールの設定は自動検出しない。
--test "コマンド" で、変えたあとに実行する単体テストのコマンド（codd.json の test）を書く（"" で消す）。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "machine"
DEST_REL = Path(".statemachine") / "codd"
IGNORE_LINE = ".codd/"
AGENT_PROMPT = SRC / "agents" / "codd-agent.md"
AGENT_DESCRIPTION = "実装と設計書の一貫性を保って変える。コードや文書の変更は必ず codd のステートマシンで進める"
AGENT_KINDS = ("kiro", "copilot")
# graphify で知識グラフを作るとき、このマシン自身を索引に入れない。
GRAPHIFY_IGNORE_LINE = ".statemachine/codd/"


def parse_ref(value: str) -> dict:
    """`名前=パス` か `パス` を refs の 1 項目にする。"""
    name, sep, path = value.partition("=")
    return {"name": name, "path": path} if sep and name and path else {"path": value}


def ref_name(ref: dict) -> str:
    return ref.get("name") or Path(str(ref["path"]).rstrip("/\\")).name


def init_repo(target: Path, side: str | None, refs: list[str] | None, gitignore: bool = True,
            scope: list[str] | None = None, ref_scopes: list[str] | None = None, discover: bool = True,
            agents: tuple[str, ...] | list[str] = AGENT_KINDS, check: str | None = None,
            test: str | None = None, exclude: list[str] | None = None,
            ref_excludes: list[str] | None = None, evidence: list[str] | None = None) -> Path:
    if not (target / ".git").exists():
        raise SystemExit(f"git リポジトリではありません: {target}")
    dest = target / DEST_REL
    config_file = dest / "codd.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else None
    existed = config is not None
    original = copy.deepcopy(config)
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
        # 同じ名前の参照先に手で書いた rules・scope・skills などは残し、名前とパスだけ入れ替える。
        old = {ref_name(r): r for r in config.get("refs", [])}
        config["refs"] = [{**old.get(ref_name(new), {}), **new} for new in map(parse_ref, refs)]
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
    if exclude is not None:
        config["exclude"] = [p for p in exclude if p]
    grouped: dict[str, list[str]] = {}
    for value in ref_excludes or []:
        name, sep, pattern = value.partition("=")
        entry = next((r for r in config.get("refs", []) if ref_name(r) == name), None)
        if not sep or entry is None:
            raise SystemExit(f"--ref-exclude は `参照先の名前=除外パターン` です（今: {value!r}）")
        patterns = grouped.setdefault(name, [])
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    for name, patterns in grouped.items():
        entry = next(r for r in config["refs"] if ref_name(r) == name)
        entry["exclude"] = patterns
    if test is not None:
        argv = shlex.split(test)
        if argv:
            config["test"] = argv
        else:
            config.pop("test", None)
    if check is not None:
        argv = shlex.split(check)
        if argv:
            config["check"] = argv
        else:
            config.pop("check", None)
    if evidence is not None:
        config["evidence"] = [p for p in evidence if p]
    if not existed:
        config.setdefault("skills", {"plan": [], "apply": []})
        config.setdefault("graphify", "auto")
    # 既にある codd.json は利用者の設定なので、渡された項目が値を変えたときだけ書き直す。
    if config != original:
        config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print("  codd.json は既にあるので書き換えていません（変えるときは手で直すか、--side などで項目を渡す）")

    write_agents(target, agents)
    if discover and not existed:
        discover_rules(target, dest)
    if gitignore:
        append_line(target / ".gitignore", IGNORE_LINE)
    append_line(target / ".graphifyignore", GRAPHIFY_IGNORE_LINE)
    return dest


def write_agents(target: Path, kinds) -> list[Path]:
    """必ずこのマシンで変えるカスタムエージェントを書く（置くたびに書き直す生成物）。"""
    prompt = AGENT_PROMPT.read_text(encoding="utf-8")
    written = []
    if "kiro" in kinds:
        path = target / ".kiro" / "agents" / "codd.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        agent = {
            "name": "codd",
            "description": AGENT_DESCRIPTION,
            "prompt": prompt,
            "tools": ["*"],
            "includeMcpJson": True,
            "resources": ["file://.statemachine/codd/workflow.yaml",
                          "skill://~/.kiro/skills/caveman/SKILL.md", "skill://~/.kiro/skills/graphify/SKILL.md"],
            # 始めるたびに参照先・守る決まり・使うスキルを読み込ませる。
            "hooks": {"agentSpawn": [{"command": "python3 .statemachine/codd/codd.py show"}]},
            "welcomeMessage": "codd: コードや文書の変更は、計画・確認・変更・検査のステートマシンで進めます",
        }
        path.write_text(json.dumps(agent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    if "copilot" in kinds:
        path = target / ".github" / "agents" / "codd.agent.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: codd\ndescription: {AGENT_DESCRIPTION}\n---\n\n{prompt}", encoding="utf-8")
        written.append(path)
    return written


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
    p = argparse.ArgumentParser(description="リポジトリに codd-agent を置いて初期設定する")
    p.add_argument("target", help="置き先のリポジトリ")
    p.add_argument("--side", choices=["impl", "design"], help="このリポジトリの側（impl = 実装 / design = 設計書）")
    p.add_argument("--ref", action="append",
                   help="参照先。`名前=パス` か `パス`（置き先からの相対でも絶対でもよい）。繰り返し可")
    p.add_argument("--scope", action="append", help="自分が受け持つフォルダ（繰り返し可。既定はリポジトリ全体）")
    p.add_argument("--ref-scope", action="append",
                   help="`参照先の名前=フォルダ`。その参照先が受け持つフォルダ（繰り返し可）")
    p.add_argument("--exclude", action="append",
                   help='自分の除外パターン（繰り返し可。一覧を入れ替える。"" で消す）')
    p.add_argument("--ref-exclude", action="append",
                   help='`参照先の名前=除外パターン`（繰り返し可。その参照先の一覧を入れ替える。名前= で消す）')
    p.add_argument("--no-discover-rules", action="store_true",
                   help="決まりらしいマークダウン（コーディングルールなど）を探して codd.json の rules に書くのをやめる")
    p.add_argument("--no-gitignore", action="store_true", help=".gitignore に .codd/ を足さない")
    p.add_argument("--agent", action="append", choices=list(AGENT_KINDS),
                   help="書くカスタムエージェント（繰り返し可。既定は kiro と copilot の両方）")
    p.add_argument("--no-agents", action="store_true", help="カスタムエージェントを書かない")
    p.add_argument("--check", metavar="コマンド",
                   help="変えたあとに実行する検査コマンド（例: \"npm test\"）。\"\" で消す")
    p.add_argument("--evidence", action="append", metavar="パス",
                   help='結果ファイルのパス・glob（繰り返し可。一覧を入れ替える。既定は扱わない。"" で消す）')
    p.add_argument("--test", metavar="コマンド",
                   help="変えたあとに実行する単体テストのコマンド（例: \"npm test\"、\"python -m unittest\"）。\"\" で消す")
    args = p.parse_args(argv)
    dest = init_repo(Path(args.target).resolve(), args.side, args.ref, gitignore=not args.no_gitignore,
                   scope=args.scope, ref_scopes=args.ref_scope, discover=not args.no_discover_rules,
                   agents=() if args.no_agents else tuple(args.agent or AGENT_KINDS), check=args.check, test=args.test,
                   exclude=args.exclude, ref_excludes=args.ref_exclude, evidence=args.evidence)
    config = json.loads((dest / "codd.json").read_text(encoding="utf-8"))
    print(f"置きました: {dest}")
    refs = config.get("refs") or [{"path": config.get("ref_path")}]
    print(f"  この側: {config['side']}  参照先: " + ", ".join(
        f"{ref_name(r)}={r['path']}" + (f"（{', '.join(r['scope'])}）" if r.get("scope") else "") for r in refs))
    if config.get("test"):
        print("  変えたあとの単体テスト: " + " ".join(config["test"]))
    if config.get("check"):
        print("  変えたあとの検査: " + " ".join(config["check"]))
    if not args.no_agents:
        print("  カスタムエージェント codd: " + "、".join(
            {"kiro": "kiro-cli chat --agent codd", "copilot": "Copilot のエージェント選択で codd"}[k]
            for k in (args.agent or AGENT_KINDS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
