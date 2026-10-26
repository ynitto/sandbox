#!/usr/bin/env python3
"""確認用フック。どのフックが発火し、どの出力が会話の文脈に入るかを確かめる。

発火したことを ~/.moltbook-probe/events.jsonl に 1 行で記録する（stdin の項目名と
値の長さだけを残し、プロンプトやツールの中身は書かない）。文脈に入るかどうかは、
きっかけごとに違う「合言葉」を出しておき、あとでエージェントに尋ねて確かめる。

  python probe_hook.py --event sessionStart --format copilot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# きっかけごとの合言葉。会話で「出てきた合言葉をすべて挙げて」と聞き、どれが届いたかを見る。
WORDS = {
    "sessionStart": "さくら", "agentSpawn": "さくら", "SessionStart": "さくら",
    "userPromptSubmitted": "つばめ", "userPromptSubmit": "つばめ", "UserPromptSubmit": "つばめ",
    "userPromptTransformed": "かえで",
    "postToolUse": "すずめ", "PostToolUse": "すずめ",
    "postToolUseFailure": "もみじ",
}
LOG_DIR = Path(os.environ.get("MOLTBOOK_PROBE_HOME", Path.home() / ".moltbook-probe"))


def _shape(value):
    """値は残さず、型と長さだけにする。"""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, list):
        return f"list({len(value)})"
    return type(value).__name__


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--client", required=True, help="copilot / kiro-v2 / kiro-v3 など、記録用の名前")
    ap.add_argument("--format", choices=["copilot", "copilot-failure", "copilot-transform", "text", "none"],
                    default="none")
    a = ap.parse_args()

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"_unparsed": raw}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "client": a.client, "event": a.event,
           "stdin_shape": _shape(payload),
           "env": {k: ("set" if os.environ.get(k) else "-") for k in ("KIRO_SESSION_ID", "COPILOT_HOME")}}
    with open(LOG_DIR / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    word = WORDS.get(a.event)
    note = f"[moltbook-probe] {a.event} の合言葉は「{word}」です。" if word else ""
    if not note or a.format == "none":
        return 0
    if a.format == "text":            # Kiro: 終了コード 0 の標準出力が文脈に入る口
        print(note)
    elif a.format == "copilot":       # Copilot: sessionStart / postToolUse の additionalContext
        print(json.dumps({"additionalContext": note}, ensure_ascii=False))
    elif a.format == "copilot-failure":   # Copilot: postToolUseFailure は終了コード 2 で stdout を足す
        print(note)
        return 2
    elif a.format == "copilot-transform":  # Copilot: userPromptTransformed で本文の末尾に足す
        base = payload.get("transformedPrompt") or payload.get("prompt") or ""
        print(json.dumps({"modifiedTransformedPrompt": f"{base}\n\n{note}"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
