"""sessions — CLI ネイティブセッションの検索と本文取得（単発・決定的・LLM なし）。

dashboard の「ノードの会話を見る」導線が使う読み取り口。セッションストアの読みは
readers の 1 実装（C7）に委ね、ここは絞り込み（CLI 名・時間窓・cwd 部分一致）と
JSON 整形だけを持つ。会話の生ログはノード外へ出さない前提（コンセプト正典 §4）なので、
このコマンドは常に**実行したその端末**で呼ばれる——他端末で実行されたノードの会話は
ここからは見えない（呼び出し側がその旨を人に示す）。
"""
from __future__ import annotations

import json

from .util import parse_iso

# 1 メッセージの本文上限（表示用の抜粋）。全文は store のパスを開けば読める。
_TEXT_CAP = 8000


def _clip(text: str) -> str:
    if len(text) <= _TEXT_CAP:
        return text
    return text[:_TEXT_CAP] + "\n…（省略。全文は store のファイルにあります）"


def cmd_sessions(args) -> int:
    from .collect import agent_defs_with_session_log
    from . import readers

    since = parse_iso(getattr(args, "since", None)) or 0.0
    until = parse_iso(getattr(args, "until", None)) or 0.0
    cwd_contains = str(getattr(args, "cwd_contains", "") or "")
    want_id = str(getattr(args, "messages", "") or "")
    limit = max(1, int(getattr(args, "limit", 20) or 20))
    only_cli = str(getattr(args, "cli", "") or "").strip().lower()

    defs = agent_defs_with_session_log()
    if only_cli:
        defs = [(n, s) for n, s in defs if n == only_cli]

    out = []
    for name, spec in defs:
        slog = spec["session_log"]
        if slog.get("format") not in readers.FORMATS:
            continue
        for sess in readers.read_sessions(slog, want_messages=bool(want_id)):
            if want_id and sess["native_id"] != want_id:
                continue
            # 時間窓は「重なり」で判定する（セッションが窓より長くても取りこぼさない）
            if since and (sess["updated_at"] or 0.0) < since:
                continue
            if until and (sess["created_at"] or 0.0) > until:
                continue
            if cwd_contains and cwd_contains not in (sess["cwd"] or ""):
                continue
            rec = {
                "agent_cli": name,
                "native_id": sess["native_id"],
                "store": sess["store"],
                "cwd": sess["cwd"],
                "created_at": sess["created_at"],
                "updated_at": sess["updated_at"],
                "model": sess["model"],
                "turns": sess["turns"],
            }
            if want_id:
                rec["messages"] = [
                    {"role": role, "text": _clip(text)} for role, text in sess["messages"]]
            out.append(rec)
    out.sort(key=lambda r: r["updated_at"] or 0.0, reverse=True)
    print(json.dumps({"sessions": out[:limit]}, ensure_ascii=False))
    return 0
