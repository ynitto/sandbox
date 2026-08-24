"""reclean — クリーニングルール改訂後の transcript 再生成（仕様書 §3）。

源泉（CLI ネイティブストア）は読み取り専用のまま残る前提を使い、既存の
transcript だけを現行の `session_log.clean` ルールで再生成する。records・
state.json（カーソル・seen・extracted）には触れない——再クリーニングが LLM 段への
再投入を引き起こすことはない。CLI 側の掃除でストアから消えたセッションは
best-effort で対象外になる。
"""
from __future__ import annotations

import os
import re

from . import readers
from .collect import _write_transcript, agent_defs_with_session_log
from .configfile import resolve_audit_dir
from .store import Store
from .util import log


def cmd_reclean(args) -> int:
    store = Store(resolve_audit_dir(args))
    only = set(getattr(args, "only_agent_cli", None) or [])
    dry = bool(getattr(args, "dry_run", False))

    updated = 0
    unreachable = 0
    for name, spec in agent_defs_with_session_log():
        if only and name not in only:
            continue
        slog = spec["session_log"]
        if slog.get("format") not in readers.FORMATS:
            continue
        cli_dir = os.path.join(store.transcripts_dir, name)
        if not os.path.isdir(cli_dir):
            continue
        # 旧 .log 形も対象にする——再生成は統一 JSONL で書かれ、旧ファイルは消える。
        existing = {os.path.splitext(n)[0] for n in os.listdir(cli_dir)
                    if n.endswith((".log", ".jsonl"))}
        if not existing:
            continue
        seen_here: "set[str]" = set()
        for sess in readers.read_sessions(slog, want_messages=True):
            safe_sid = re.sub(r"[^A-Za-z0-9._-]", "_", sess["native_id"])[:64]
            if safe_sid not in existing:
                continue         # このセッションの transcript は未保存（対象外）
            seen_here.add(safe_sid)
            if not dry:
                _write_transcript(store, name, sess)
            updated += 1
        unreachable += len(existing - seen_here)

    label = "再生成対象" if dry else "再生成"
    log("reclean", f"transcript {label}: {updated} 件"
                    + (f"（源泉から消えて対象外: {unreachable} 件）" if unreachable else ""))
    return 0
