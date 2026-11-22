#!/usr/bin/env python3
"""reverse-spec を置く: 既存のソースから仕様書を起こす作業場（仕様書リポジトリ）を作る。

    python3 tools/reverse-spec/init.py <ソースのフォルダ> [--out <仕様書のフォルダ>] [--host claude,codex,...]

- `<out>/.rspec/` に駆動スクリプト（rspec.py）・段の指示・ひな形を写す（置き直すたびに入れ替える）
- `<out>/rspec.json` に設定を書く（すでにあれば残し、渡した引数の項目だけを書き換える）
- エージェントの入口（AGENTS.md と、エージェントごとの読み込み先・検証用サブエージェント）を書く
- 仕様書リポジトリを git で始め、ソースを数え上げてコミットする

依存は python3 と git だけ。ソースは読むだけで、何も書き込まない。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MACHINE = HERE / "machine"
HOSTS = ("claude", "codex", "copilot", "kiro", "gemini")
VERIFIER_DESC = "reverse-spec の検証の用紙を受け取り、根拠の行だけを読んで主張を OK / NG で判定する（仕様書は読まない）"

GITIGNORE = """\
# reverse-spec（init.py が書く）
.rspec/logs/
.rspec/*.lock
.rspec/run-token.json
.rspec/**/*.tmp
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def place_machine(out: Path) -> None:
    dest = out / ".rspec"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("actions", "templates"):
        if (dest / name).exists():
            shutil.rmtree(dest / name)
        shutil.copytree(MACHINE / name, dest / name)
    shutil.copy2(MACHINE / "rspec.py", dest / "rspec.py")


def write_hosts(out: Path, hosts: list) -> list:
    agents = (MACHINE / "agents" / "AGENTS.md").read_text(encoding="utf-8")
    verifier = (MACHINE / "agents" / "rspec-verifier.md").read_text(encoding="utf-8")
    written = [out / "AGENTS.md"]
    write_text(out / "AGENTS.md", agents)  # Codex・GitHub Copilot・Kiro などはこれを直接読む
    if "claude" in hosts:
        write_text(out / "CLAUDE.md", "@AGENTS.md\n")
        write_text(out / ".claude" / "agents" / "rspec-verifier.md",
                   f"---\nname: rspec-verifier\ndescription: {VERIFIER_DESC}\ntools: Read, Grep, Glob, Edit\n---\n\n{verifier}")
        written += [out / "CLAUDE.md", out / ".claude" / "agents" / "rspec-verifier.md"]
    if "gemini" in hosts:
        write_text(out / "GEMINI.md", "@AGENTS.md\n")
        written.append(out / "GEMINI.md")
    if "copilot" in hosts:
        path = out / ".github" / "agents" / "rspec-verifier.agent.md"
        write_text(path, f"---\nname: rspec-verifier\ndescription: {VERIFIER_DESC}\n"
                         f"tools: ['read', 'edit', 'search']\nuser-invocable: false\n---\n\n{verifier}")
        written.append(path)
    if "kiro" in hosts:
        path = out / ".kiro" / "agents" / "rspec-verifier.json"
        write_text(path, json.dumps({"name": "rspec-verifier", "description": VERIFIER_DESC, "prompt": verifier,
                                     "tools": ["*"]}, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
    return written


def update_config(out: Path, src: Path, args) -> dict:
    path = out / "rspec.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    fresh = not cfg
    try:
        rel = os.path.relpath(src, out)
    except ValueError:  # Windows で別のドライブ
        rel = str(src)
    if fresh or args.source_update:
        cfg["source"] = Path(rel).as_posix()
    sys.path.insert(0, str(MACHINE))
    import rspec  # noqa: E402  既定値はスクリプトと同じものを使う
    for k, v in rspec.DEFAULT_CONFIG.items():
        if k != "source":
            cfg.setdefault(k, v)
    if args.exclude is not None:
        cfg["exclude"] = list(rspec.DEFAULT_CONFIG["exclude"]) + [x for x in args.exclude if x]
    if args.confirm:
        cfg["confirm"] = args.confirm
    if args.on_stuck:
        cfg["on_stuck"] = args.on_stuck
    if args.no_commit:
        cfg["auto_commit"] = False
    write_text(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return cfg


def git(out: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(out), capture_output=True, text=True, encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="既存のソースから仕様書を起こす作業場を作る")
    ap.add_argument("source", help="ソースのフォルダ（読むだけ）")
    ap.add_argument("--out", help="仕様書のフォルダ（既定は <ソース>-spec）")
    ap.add_argument("--host", default="all", help=f"エージェントの入口を書く対象（{','.join(HOSTS)} か all。AGENTS.md はいつも書く）")
    ap.add_argument("--exclude", action="append", help="数え上げから外すパターン（繰り返せる。既定の除外に足す）")
    ap.add_argument("--confirm", choices=["human", "auto"], help="単位の分け方を人が確かめるか（auto は無人で進む）")
    ap.add_argument("--on-stuck", choices=["ask", "skip"], help="単位で止まったら訊くか、止めて次の単位へ進むか")
    ap.add_argument("--no-commit", action="store_true", help="段ごとのコミットをしない")
    ap.add_argument("--source-update", action="store_true", help="置き直すときに rspec.json の source も書き換える")
    ap.add_argument("--no-git", action="store_true", help="git で始めない")
    args = ap.parse_args(argv)

    src = Path(args.source).expanduser().resolve()
    if not src.is_dir():
        print(f"ソースのフォルダがありません: {src}", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser().resolve() if args.out else src.with_name(src.name + "-spec")
    if out == src or src in out.parents:
        print("仕様書のフォルダはソースの外に置いてください（ソースは読むだけにする）", file=sys.stderr)
        return 2
    hosts = list(HOSTS) if args.host == "all" else [h.strip() for h in args.host.split(",") if h.strip()]
    unknown = [h for h in hosts if h not in HOSTS]
    if unknown:
        print(f"知らない対象: {', '.join(unknown)}（{', '.join(HOSTS)}）", file=sys.stderr)
        return 2
    again = (out / ".rspec" / "rspec.py").exists()
    out.mkdir(parents=True, exist_ok=True)
    place_machine(out)
    update_config(out, src, args)
    written = write_hosts(out, hosts)
    gi = out / ".gitignore"
    if "# reverse-spec" not in (gi.read_text(encoding="utf-8") if gi.exists() else ""):
        with gi.open("a", encoding="utf-8", newline="\n") as f:
            f.write(GITIGNORE)
    if not (out / ".gitattributes").exists():
        write_text(out / ".gitattributes", "* text=auto eol=lf\n")
    for d in ("specs", "extraction"):
        (out / d).mkdir(exist_ok=True)

    tool = [sys.executable, str(out / ".rspec" / "rspec.py")]
    if not again or not (out / "extraction" / "inventory.tsv").exists():
        proc = subprocess.run(tool + ["ingest"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(proc.stdout.strip() or proc.stderr.strip())
        if proc.returncode != 0:
            return proc.returncode
    if not args.no_git:
        if not (out / ".git").exists():
            git(out, "init", "-q")
        git(out, "add", "-A")
        if git(out, "status", "--porcelain").stdout.strip():
            ident = [] if git(out, "config", "user.email").stdout.strip() else ["-c", "user.name=rspec", "-c", "user.email=rspec@localhost"]
            git(out, *ident, "commit", "-q", "-m", "rspec: 置き直した" if again else "rspec: 作業場を作った")

    print(f"{'置き直しました' if again else '作りました'}: {out}")
    for path in written:
        print(f"  {path.relative_to(out)}")
    print("\n次に、エージェントをこのフォルダで開いて「reverse-spec を進めて」と頼むか、無人で回します:")
    print(f"  cd {out}")
    print("  python3 .rspec/rspec.py step                 # 対話で進める（エージェントに実行させる）")
    print("  python3 .rspec/rspec.py run --agent claude   # 段ごとに新しいエージェントを起こして回す（codex / copilot / kiro / gemini）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
