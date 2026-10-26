#!/usr/bin/env python3
"""確認用のフック・指示ファイルを置く／取り除く。

  python setup_probe.py --target copilot-user          # Windows で実行
  python setup_probe.py --target copilot-plugin        # Windows で実行
  python setup_probe.py --target copilot-instructions  # Windows で実行
  python3 setup_probe.py --target kiro-v2              # WSL で実行
  python3 setup_probe.py --target kiro-v3              # WSL で実行
  python3 setup_probe.py --target kiro-steering        # WSL で実行
  python setup_probe.py --remove                       # このスクリプトが置いたものを全部消す

置いたファイルは ~/.moltbook-probe/created.json に記録し、--remove はそれだけを消す。
既存のファイルは書き換えない（指示ファイルだけは印で囲んだ段落を足し、--remove で外す）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "probe_hook.py"
HOME = Path(os.environ.get("MOLTBOOK_PROBE_HOME", Path.home() / ".moltbook-probe"))
LEDGER = HOME / "created.json"
COPILOT_HOME = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
KIRO_HOME = Path.home() / ".kiro"
BEGIN, END = "<!-- moltbook-probe:begin -->", "<!-- moltbook-probe:end -->"

# Copilot の各きっかけと、出力の形（probe_hook.py --format）
COPILOT_EVENTS = {
    "sessionStart": "copilot", "userPromptSubmitted": "copilot", "userPromptTransformed": "copilot-transform",
    "postToolUse": "copilot", "postToolUseFailure": "copilot-failure", "agentStop": "none", "sessionEnd": "none",
}
KIRO_V2_EVENTS = {"agentSpawn": "text", "userPromptSubmit": "text", "postToolUse": "text", "stop": "none"}
KIRO_V3_EVENTS = {"SessionStart": "text", "UserPromptSubmit": "text", "PostToolUse": "text",
                  "Stop": "none", "SessionEnd": "none"}


def _ledger() -> list:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _record(path: Path, kind: str = "file") -> None:
    items = _ledger()
    items.append({"path": str(path), "kind": kind})
    HOME.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_new(path: Path, text: str) -> None:
    if path.exists():
        sys.exit(f"既にあるので書き換えません: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _record(path)
    print(f"置きました: {path}")


def _cmd(event: str, client: str, fmt: str) -> tuple[str, str]:
    py = sys.executable
    args = f'--event {event} --client {client} --format {fmt}'
    return f'"{py}" "{HOOK}" {args}', f'& "{py}" "{HOOK}" {args}'


def _copilot_hooks() -> dict:
    hooks = {}
    for ev, fmt in COPILOT_EVENTS.items():
        bash, ps = _cmd(ev, "copilot", fmt)
        hooks[ev] = [{"type": "command", "bash": bash, "powershell": ps, "timeoutSec": 10}]
    return {"version": 1, "hooks": hooks}


def setup(target: str) -> None:
    if target == "copilot-user":
        _write_new(COPILOT_HOME / "hooks" / "moltbook-probe.json",
                   json.dumps(_copilot_hooks(), ensure_ascii=False, indent=2))
    elif target == "copilot-plugin":
        root = HOME / "marketplace"
        plugin = root / "plugins" / "moltbook-probe"
        _write_new(plugin / "hooks.json", json.dumps(_copilot_hooks(), ensure_ascii=False, indent=2))
        _write_new(plugin / "plugin.json", json.dumps(
            {"name": "moltbook-probe", "version": "0.1.0", "description": "Moltbook の確認用フック",
             "hooks": "hooks.json"}, ensure_ascii=False, indent=2))
        _write_new(root / ".github" / "plugin" / "marketplace.json", json.dumps(
            {"name": "moltbook-probe", "owner": {"name": "moltbook"},
             "plugins": [{"name": "moltbook-probe", "source": "./plugins/moltbook-probe", "strict": False}]},
            ensure_ascii=False, indent=2))
        print(f"\nアプリ: Customize → Plugins → 歯車 → マーケットプレイスの追加で、次を指定してください:\n  {root}\n"
              f"CLI があれば: copilot plugin marketplace add \"{root}\" のあと "
              "copilot plugin install moltbook-probe@moltbook-probe")
    elif target == "copilot-instructions":
        path = COPILOT_HOME / "copilot-instructions.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if BEGIN in text:
            sys.exit("既に置いてあります")
        block = f"\n{BEGIN}\n確認用: 指示ファイルの合言葉は「あやめ」です。\n{END}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + block, encoding="utf-8")
        _record(path, "block")
        print(f"段落を足しました: {path}")
    elif target == "kiro-v2":
        hooks = {}
        for ev, fmt in KIRO_V2_EVENTS.items():
            hooks[ev] = [{"command": _cmd(ev, "kiro-v2", fmt)[0], "timeout_ms": 10000}]
        _write_new(KIRO_HOME / "agents" / "moltbook-probe.json", json.dumps(
            {"name": "moltbook-probe", "description": "Moltbook の確認用エージェント",
             "tools": ["*"], "hooks": hooks}, ensure_ascii=False, indent=2))
        print("試すとき: kiro-cli chat --agent moltbook-probe（既定のエージェントは変えません）")
    elif target == "kiro-v3":
        for ev, fmt in KIRO_V3_EVENTS.items():
            _write_new(KIRO_HOME / "hooks" / f"moltbook-probe-{ev}.json", json.dumps(
                {"version": "1", "hooks": [{"name": f"moltbook-probe-{ev}", "trigger": ev, "enabled": True,
                                            "timeout": 10,
                                            "action": {"type": "command", "command": _cmd(ev, "kiro-v3", fmt)[0]}}]},
                ensure_ascii=False, indent=2))
        print("試すとき: kiro-cli chat --v3")
    elif target == "kiro-steering":
        _write_new(KIRO_HOME / "steering" / "moltbook-probe.md",
                   "# 確認用\n\nグローバルの steering の合言葉は「ひのき」です。\n")
    else:
        sys.exit(f"不明な target: {target}")


def remove() -> None:
    for item in reversed(_ledger()):
        path = Path(item["path"])
        if item["kind"] == "block" and path.exists():
            text = path.read_text(encoding="utf-8")
            if BEGIN in text and END in text:
                head, rest = text.split(BEGIN, 1)
                tail = rest.split(END, 1)[1]
                # 置くときに前後へ 1 つずつ足した改行だけを外し、元の改行は残す
                left = (head[:-1] if head.endswith("\n") else head) + (tail[1:] if tail.startswith("\n") else tail)
                if left.strip():
                    path.write_text(left, encoding="utf-8")
                    print(f"段落を外しました: {path}")
                else:   # このスクリプトが作った空のファイルなら消す
                    path.unlink()
                    print(f"消しました: {path}")
        elif path.exists():
            path.unlink()
            print(f"消しました: {path}")
    if LEDGER.exists():
        LEDGER.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", choices=["copilot-user", "copilot-plugin", "copilot-instructions",
                                        "kiro-v2", "kiro-v3", "kiro-steering"])
    g.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    remove() if a.remove else setup(a.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
