#!/usr/bin/env python3
"""リポジトリに codd-agent を置いて初期設定する（置き先の名前は `.statemachine/codd/`。確かめる・手入れする
ステートマシンを隣の `.statemachine/codd-review/` に置く）。

リポジトリごとに 1 回（と、新しい版を置き直すとき）実行する。graphify などの外部のミドルウェアを端末に入れるのは
`install.py` の仕事（端末ごとに 1 回）。

    python3 tools/codd-agent/init.py <実装のリポジトリ> --side impl --ref docs=../my-design
    python3 tools/codd-agent/init.py <設計書のリポジトリ> --side design --ref ../my-impl
    python3 tools/codd-agent/init.py <実装のリポジトリ> --side impl --ref api=../api-docs --ref ui=../ui-docs

    # 実装と設計書が同じリポジトリにあるとき（設計書は docs/、それ以外を実装として受け持つ）
    python3 tools/codd-agent/init.py <リポジトリ>
    python3 tools/codd-agent/init.py <リポジトリ> --ref-scope docs=design   # 設計書のフォルダが docs/ でないとき

--ref は `名前=パス` か `パス`（名前はフォルダ名）。いくつでも渡せる。
--scope は自分が受け持つフォルダ、--ref-scope は `参照先の名前=フォルダ` で参照先が受け持つフォルダ（どちらも繰り返し可）。
初めて置くときに --ref を渡さなければ、同じリポジトリの docs/（--ref-scope docs=フォルダ で変える）を参照先 docs にし、
--side の既定は impl、自分はそのフォルダの外の全部を受け持つ（--scope で絞れる）。
docs/ も無ければ（--ref-scope も渡さなければ）、文書を持たないリポジトリとして置き（refs は空）、実装とテストだけの一貫性を取る。
--exclude は自分の除外パターン、--ref-exclude は `参照先の名前=除外パターン`（どちらも繰り返し可）。
渡した側の一覧を入れ替え、--exclude "" / --ref-exclude 名前= で空にする。

`<リポジトリ>/.statemachine/codd/` に machine/ の中身を写し、codd.json を書く。
既に置いてあれば定義とスクリプトを入れ替え（古いファイルは消す）、codd.json は上書きしない。--side / --ref / --scope /
--ref-scope / --exclude / --ref-exclude / --check / --test / --evidence を渡したときだけ、その項目を書き換える（--ref は参照先の一覧を入れ替えるが、同じ名前の
参照先に手で書いた rules・scope・exclude などは残す）。従う手順（決まり・スキル・道具）は codd.json の guides を手で書く。
`.codd/`（探した結果・検査の記録・変える前の写し・graphify のグラフ）は .gitignore に足す（--no-gitignore で足さない）。
このマシン自身が graphify の索引に入らないよう、.graphifyignore に `.statemachine/codd/` を足す。
初めて置いたときは、自分と参照先から決まりらしいマークダウン（コーディングルールなど）を探して codd.json の rules /
refs[].rules に書く（--no-discover-rules でやめる。あとからは `codd.py rules --write`）。
kiro-cli と GitHub Copilot 向けに、必ずこのマシンで変えるカスタムエージェント `codd` を書く
（`.kiro/agents/codd.json` と `.github/agents/codd.agent.md`。--agent で絞り、--no-agents で書かない）。
変える段を受け持つサブエージェント `codd-apply` も並べて書く（codd.json の subagent が true のときに codd が呼ぶ）。
文書の意味を抜き出す束を受け持つサブエージェント `codd-graph` も書く（graphify が {"semantic": "session"} のときに呼ぶ）。
--check "コマンド" で、変えたあとに実行する検査コマンド（codd.json の check）を書く（繰り返すと書いた順に動かす）。
--evidence "パス" で結果ファイルの一覧を指定する（繰り返し可。"" で扱わない）。検査ツールの設定は自動検出しない。
--test "コマンド" で、変えたあとに実行するテストのコマンド（codd.json の test）を書く（繰り返すと書いた順に動かす。"" で消す）。
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
# 確かめる・手入れするステートマシン（codd-review）。本流と別のフォルダに置く（定期実行の道具は
# `.statemachine/<名前>/workflow.yaml` を 1 つのタスクとして並べるため）。codd.py は本流のものを使う。
REVIEW_SRC = Path(__file__).resolve().parent / "review"
REVIEW_REL = Path(".statemachine") / "codd-review"
# --ref を渡さずに初めて置くとき（実装と設計書が同じリポジトリ）の参照先と、設計書の既定のフォルダ。
SAME_REPO_REF = "docs=."
SAME_REPO_DOCS = "docs"
IGNORE_LINE = ".codd/"
AGENT_PROMPT = SRC / "agents" / "codd-agent.md"
AGENT_DESCRIPTION = "実装と設計書の一貫性を保って変える。コードや文書の変更は必ず codd のステートマシンで進める"
# 変える段を 1 段ずつ受け持つサブエージェント（codd.json の subagent が true のときに codd が呼ぶ）。
# モデルは書かない（書かなければ呼び出し元と同じモデルで動く）。
APPLY_AGENT_PROMPT = SRC / "agents" / "codd-apply.md"
APPLY_AGENT_DESCRIPTION = "codd が呼ぶ。承認された計画の、今の段のファイルだけを変える"
# 文書の意味を抜き出す束を 1 つずつ受け持つサブエージェント（codd.json の graphify が {"semantic": "session"} で、
# subagent が true のときに codd が呼ぶ）。モデルは書かない。
GRAPH_AGENT_PROMPT = SRC / "agents" / "codd-graph.md"
GRAPH_AGENT_DESCRIPTION = "codd が呼ぶ。グラフの束の文書を読み、知識グラフの断片（JSON）を書く"
AGENT_KINDS = ("kiro", "copilot")
# graphify で知識グラフを作るとき、このマシン自身を索引に入れない。
GRAPHIFY_IGNORE_LINES = (".statemachine/codd/", ".statemachine/codd-review/")


def parse_ref(value: str) -> dict:
    """`名前=パス` か `パス` を refs の 1 項目にする。"""
    name, sep, path = value.partition("=")
    return {"name": name, "path": path} if sep and name and path else {"path": value}


def ref_name(ref: dict) -> str:
    return ref.get("name") or Path(str(ref["path"]).rstrip("/\\")).name


def init_repo(target: Path, side: str | None, refs: list[str] | None, gitignore: bool = True,
            scope: list[str] | None = None, ref_scopes: list[str] | None = None, discover: bool = True,
            agents: tuple[str, ...] | list[str] = AGENT_KINDS, check: str | list[str] | None = None,
            test: str | list[str] | None = None, exclude: list[str] | None = None,
            ref_excludes: list[str] | None = None, evidence: list[str] | None = None) -> Path:
    if not (target / ".git").exists():
        raise SystemExit(f"git リポジトリではありません: {target}")
    dest = target / DEST_REL
    config_file = dest / "codd.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else None
    existed = config is not None
    original = copy.deepcopy(config)
    code_only = False
    if config is None and not refs:
        # 実装と設計書が同じリポジトリ: 設計書のフォルダを参照先 docs にし、自分はその外の全部を受け持つ。
        # 設計書のフォルダも無ければ、文書を持たないリポジトリとして実装とテストだけの一貫性を取る（refs は空）。
        side = side or "impl"
        name = SAME_REPO_REF.split("=")[0]
        if not ref_scopes and not (target / SAME_REPO_DOCS).is_dir():
            code_only = True
        else:
            refs = [SAME_REPO_REF]
            ref_scopes = ref_scopes or [f"{name}={SAME_REPO_DOCS}"]
            missing = [v.partition("=")[2] for v in ref_scopes if not (target / v.partition("=")[2]).is_dir()]
            if missing:
                raise SystemExit(f"設計書のフォルダがありません: {', '.join(missing)}（--ref-scope {name}=フォルダ で指定するか、"
                                 "別のリポジトリなら --ref で参照先を指定してください）")
    if config is None and side is None:
        raise SystemExit("初めて置くときは --side を指定してください")

    dest.mkdir(parents=True, exist_ok=True)
    # 古い版のファイルを残さない（codd.json は利用者の設定、links.json は見つけたつながりの控えなので残す）。
    for item in dest.iterdir():
        if item.name in ("codd.json", "links.json"):
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
    review = target / REVIEW_REL
    shutil.rmtree(review, ignore_errors=True)   # 古い版の手順を残さない（設定は持たない）
    shutil.copytree(REVIEW_SRC, review, ignore=shutil.ignore_patterns("__pycache__"))

    config = config or {}
    if code_only:
        config["refs"] = []
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
    for key, value in (("test", test), ("check", check)):
        if value is None:
            continue
        commands = [argv for argv in map(shlex.split, [value] if isinstance(value, str) else value) if argv]
        if commands:
            config[key] = commands[0] if len(commands) == 1 else commands
        else:
            config.pop(key, None)
    if evidence is not None:
        config["evidence"] = [p for p in evidence if p]
    if not existed:
        config.setdefault("guides", [])
        config.setdefault("graphify", "auto")
    # 既にある codd.json は利用者の設定なので、渡された項目が値を変えたときだけ書き直す。
    if config != original:
        write_text(config_file, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    else:
        print("  codd.json は既にあるので書き換えていません（変えるときは手で直すか、--side などで項目を渡す）")

    write_agents(target, agents)
    if discover and not existed:
        discover_rules(target, dest)
    if gitignore:
        append_line(target / ".gitignore", IGNORE_LINE)
    for line in GRAPHIFY_IGNORE_LINES:
        append_line(target / ".graphifyignore", line)
    return dest


def guess_test(target: Path) -> str | None:
    """よくある置き方からテストのコマンドを推す。示すだけで、codd.json には書かない。"""
    try:
        scripts = json.loads((target / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return "npm test"
    except (OSError, ValueError, AttributeError):
        pass
    if (target / "pytest.ini").is_file() or "[tool.pytest" in _read(target / "pyproject.toml"):
        return "python -m pytest"
    if (target / "go.mod").is_file():
        return "go test ./..."
    if (target / "Cargo.toml").is_file():
        return "cargo test"
    if re.search(r"^test\s*:", _read(target / "Makefile"), re.M):
        return "make test"
    tests = [*target.glob("tests/test_*.py"), *target.glob("test/test_*.py")]
    if tests:
        return "python -m unittest" if all("unittest" in _read(f) for f in tests) else "python -m pytest"
    return None


# 変えればテストの合否を左右できるファイル（テストの設定・CI・依存の宣言）。protect の候補として示すだけで書かない。
PROTECT_CANDIDATES = (
    "**/conftest.py", "pytest.ini", "tox.ini", "jest.config.*", "vitest.config.*", "playwright.config.*",
    "cypress.config.*", "karma.conf.*", ".mocharc.*",
    ".github/workflows/", ".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".circleci/",
    "package.json", "pyproject.toml", "requirements*.txt", "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle*",
)


def guess_protect(target: Path) -> list[str]:
    """リポジトリにある PROTECT_CANDIDATES。フォルダの候補は、フォルダがあるときだけ挙げる。"""
    def present(c: str) -> bool:
        if c.endswith("/"):
            return (target / c).is_dir()
        if c.startswith("**/"):   # 依存の置き場（node_modules・.venv）までたどらない
            name = c[3:]
            return any(target.glob(name)) or any(d.glob(f"**/{name}") for d in (target / "tests", target / "test")
                                                 if d.is_dir())
        return any(target.glob(c))
    return [c for c in PROTECT_CANDIDATES if present(c)]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write_agents(target: Path, kinds) -> list[Path]:
    """必ずこのマシンで変えるカスタムエージェントを書く（置くたびに書き直す生成物）。"""
    prompt = AGENT_PROMPT.read_text(encoding="utf-8")
    apply_prompt = APPLY_AGENT_PROMPT.read_text(encoding="utf-8")
    graph_prompt = GRAPH_AGENT_PROMPT.read_text(encoding="utf-8")
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
            "resources": ["file://.statemachine/codd/workflow.yaml", "file://.statemachine/codd-review/workflow.yaml",
                          "skill://~/.kiro/skills/caveman/SKILL.md", "skill://~/.kiro/skills/graphify/SKILL.md"],
            # 始めるたびに参照先・守る決まり・使うスキルを読み込ませる。
            "hooks": {"agentSpawn": [{"command": "python3 .statemachine/codd/codd.py show"}]},
            "welcomeMessage": "codd: コードや文書の変更は、計画・確認・変更・検査のステートマシンで進めます",
        }
        write_text(path, json.dumps(agent, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
        path = target / ".kiro" / "agents" / "codd-apply.json"
        worker = {"name": "codd-apply", "description": APPLY_AGENT_DESCRIPTION, "prompt": apply_prompt,
                  "tools": ["*"], "includeMcpJson": True}
        write_text(path, json.dumps(worker, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
        path = target / ".kiro" / "agents" / "codd-graph.json"
        worker = {"name": "codd-graph", "description": GRAPH_AGENT_DESCRIPTION, "prompt": graph_prompt,
                  "tools": ["*"], "includeMcpJson": True}
        write_text(path, json.dumps(worker, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
    if "copilot" in kinds:
        path = target / ".github" / "agents" / "codd.agent.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text(path, f"---\nname: codd\ndescription: {AGENT_DESCRIPTION}\nagents: [codd-apply, codd-graph]\n---\n\n"
                         f"{prompt}")
        written.append(path)
        path = target / ".github" / "agents" / "codd-apply.agent.md"
        write_text(path, f"---\nname: codd-apply\ndescription: {APPLY_AGENT_DESCRIPTION}\nuser-invocable: false\n---\n\n"
                         f"{apply_prompt}")
        written.append(path)
        path = target / ".github" / "agents" / "codd-graph.agent.md"
        write_text(path, f"---\nname: codd-graph\ndescription: {GRAPH_AGENT_DESCRIPTION}\nuser-invocable: false\n---\n\n"
                         f"{graph_prompt}")
        written.append(path)
    return written


def discover_rules(target: Path, dest: Path) -> None:
    """自分と参照先から決まりらしいマークダウン（コーディングルールなど）を探し、codd.json の guides に書く。"""
    proc = subprocess.run([sys.executable, str(dest / "codd.py"), "rules", "--write"], cwd=target,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        reason = (proc.stderr.strip().splitlines() or [""])[-1]
        print("  決まりの候補は探せませんでした（参照先を置いてから `python3 .statemachine/codd/codd.py rules --write`）: "
              + reason, file=sys.stderr)
        return
    if "件を" in proc.stdout:
        print("  決まりの候補を codd.json の guides に書きました（決まりでないものは手で消してください）:")
        start = proc.stdout.find("候補:")
        print("\n".join("  " + ln for ln in proc.stdout[start:].splitlines()[1:] if ln.startswith("  - ")))


def write_text(path: Path, text: str) -> None:
    """改行は元のファイルに合わせ、新しいファイルは LF で書く（Windows のテキストモードは既定で \\n を CRLF にする）。"""
    newline = "\r\n" if path.is_file() and b"\r\n" in path.read_bytes() else "\n"
    with path.open("w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def append_line(path: Path, line: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if line in lines:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    prefix = "" if not text or text.endswith("\n") else "\n"
    write_text(path, f"{text}{prefix}{line}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="リポジトリに codd-agent を置いて初期設定する")
    p.add_argument("target", help="置き先のリポジトリ")
    p.add_argument("--side", choices=["impl", "design"], help="このリポジトリの側（impl = 実装 / design = 設計書）")
    p.add_argument("--ref", action="append",
                   help="参照先。`名前=パス` か `パス`（置き先からの相対でも絶対でもよい）。繰り返し可。"
                        "初めて置くときに渡さなければ、同じリポジトリの docs/ を参照先 docs にする")
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
    p.add_argument("--check", action="append", metavar="コマンド",
                   help="変えたあとに実行する検査コマンド（例: \"npm run lint\"）。繰り返すと書いた順に動かす。\"\" で消す")
    p.add_argument("--evidence", action="append", metavar="パス",
                   help='結果ファイルのパス・glob（繰り返し可。一覧を入れ替える。既定は扱わない。"" で消す）')
    p.add_argument("--test", action="append", metavar="コマンド",
                   help="変えたあとに実行するテストのコマンド（例: \"npm test\"、\"python -m unittest\"）。"
                        "繰り返すと書いた順に動かす。\"\" で消す")
    args = p.parse_args(argv)
    dest = init_repo(Path(args.target).resolve(), args.side, args.ref, gitignore=not args.no_gitignore,
                   scope=args.scope, ref_scopes=args.ref_scope, discover=not args.no_discover_rules,
                   agents=() if args.no_agents else tuple(args.agent or AGENT_KINDS), check=args.check, test=args.test,
                   exclude=args.exclude, ref_excludes=args.ref_exclude, evidence=args.evidence)
    config = json.loads((dest / "codd.json").read_text(encoding="utf-8-sig"))
    print(f"置きました: {dest}")
    refs = config["refs"] if "refs" in config else [{"path": config.get("ref_path")}]
    print(f"  この側: {config['side']}  参照先: " + (", ".join(
        f"{ref_name(r)}={r['path']}" + (f"（{', '.join(r['scope'])}）" if r.get("scope") else "") for r in refs)
        or "なし（文書を持たないリポジトリ。実装とテストの一貫性だけを取る）"))
    test = config.get("test")
    if isinstance(test, dict):
        for name, argv in test.items():
            print(f"  変えたあとのテスト（{name}）: " + " ".join(argv))
    elif test:
        for argv in [test] if isinstance(test[0], str) else test:
            print("  変えたあとのテスト: " + " ".join(argv))
    else:
        guess = guess_test(Path(args.target).resolve())
        print("  変えたあとのテスト: 未設定（テストを動かさずに検査が通る）"
              + (f"。設定するなら --test \"{guess}\"" if guess else "。--test \"コマンド\" で設定する"))
    if not config.get("protect"):
        found = guess_protect(Path(args.target).resolve())
        if found:
            print("  人の承認が要るファイル（protect）: 未設定。テストの設定・CI・依存の宣言を、テストを通すために"
                  "変えさせないなら codd.json に書く: \"protect\": " + json.dumps(found, ensure_ascii=False))
    check = config.get("check") or []
    for argv in [check] if check and isinstance(check[0], str) else check:
        print("  変えたあとの検査: " + " ".join(argv))
    if not args.no_agents:
        print("  カスタムエージェント codd: " + "、".join(
            {"kiro": "kiro-cli chat --agent codd", "copilot": "Copilot のエージェント選択で codd"}[k]
            for k in (args.agent or AGENT_KINDS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
