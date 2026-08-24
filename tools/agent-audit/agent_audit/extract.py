"""extract — レコード → 観測（map・弱モデル可。仕様書 §6）。

対象の選抜・入力の切り詰め・ゲート判定はすべて決定的。LLM は 1 レコードにつき
最大 2 回（本試行 + JSON 修復 1 回）。ゲートを通らない実行は LLM を呼ばずに終わる。
"""
from __future__ import annotations

import time

from .configfile import resolve_audit_dir
from .llm import LlmBlocked, LlmError, parse_json_reply, run_llm
from .store import Store, observation_id
from .util import elog, log, now_iso

OBSERVATION_KINDS = ("learn", "avoid", "skill-gap", "prompt-issue", "config-issue")

_PROMPT = """あなたはエージェント実行証跡の監査係です。以下の 1 件の実行レコードを読み、
今後の実行を改善しうる観測（learn / avoid / skill-gap / prompt-issue / config-issue）を
抽出してください。確かな根拠がレコードに無い観測は出さないでください。無ければ空配列。

出力は次の JSON だけ（説明文・コードフェンス不要）:
{"observations": [{"kind": "learn|avoid|skill-gap|prompt-issue|config-issue",
                   "text": "<一般化した観測文（日本語・1〜2 文）>"}]}

レコード:
"""

_REPAIR = """先ほどの出力は要求した JSON 契約に一致しませんでした。
説明文を付けず、次の形の JSON だけを出力し直してください:
{"observations": [{"kind": "...", "text": "..."}]}

先ほどの出力（先頭 400 字）:
"""


def is_candidate(rec: dict, filters: "list[str]") -> bool:
    f = set(filters or [])
    status = str(rec.get("status") or "")
    if "failed" in f and (status == "failed" or rec.get("error_class")):
        return True
    if "retried" in f:
        try:
            if int(rec.get("retries") or 0) >= 2:
                return True
        except (TypeError, ValueError):
            pass
    if "verify-flip" in f and str(rec.get("verify") or "") == "fail":
        return True
    if "needs" in f:
        try:
            if int(rec.get("escalations") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    if "long-session" in f and rec.get("kind") == "session":
        try:
            if int(rec.get("turns") or 0) >= 30 or float(rec.get("seconds") or 0) >= 1800:
                return True
        except (TypeError, ValueError):
            pass
    return False


def record_digest(store: Store, rec: dict, limit_chars: int) -> str:
    """LLM へ渡すダイジェスト。transcript があれば末尾を limit の半分まで添える。"""
    import json as _json
    head = {k: v for k, v in rec.items()
            if k in ("id", "ts", "kind", "source", "tool", "workload", "ref", "status",
                     "error_class", "retries", "verify", "turns", "seconds",
                     "agent_cli", "model", "note", "escalations")}
    text = _json.dumps(head, ensure_ascii=False, indent=1)
    excerpt_ref = rec.get("excerpt_ref")
    if excerpt_ref:
        import os

        from .collect import transcript_text
        path = os.path.join(store.root, excerpt_ref)
        try:
            body = transcript_text(path)
            tail = body[-max(limit_chars // 2, 500):]
            text += "\n\ntranscript（末尾抜粋）:\n" + tail
        except OSError:
            pass
    return text[:limit_chars]


def gate_reason(args, store: Store, candidates: "list[dict]") -> "str | None":
    """間隔・蓄積ゲート（仕様書 §6）。通らない理由を返す（None = 通過）。"""
    if getattr(args, "force", False):
        return None
    interval_h = float(getattr(args, "extract_min_interval_hours", 6.0) or 0.0)
    if interval_h > 0:
        elapsed = time.time() - store.last_run("extract")
        if elapsed < interval_h * 3600.0:
            return (f"間隔ゲート: 前回 extract から {elapsed / 3600.0:.1f}h "
                    f"< {interval_h}h（--force で強制）")
    min_records = int(getattr(args, "extract_min_records", 10) or 0)
    if min_records > 0 and len(candidates) < min_records:
        return (f"蓄積ゲート: 未抽出の候補 {len(candidates)} 件 "
                f"< {min_records} 件（--force で強制）")
    return None


def cmd_extract(args) -> int:
    store = Store(resolve_audit_dir(args))
    filters = list(getattr(args, "extract_filters", None) or [])
    candidates = [rec for rec in store.iter_records()
                  if rec.get("id") and rec["id"] not in store.state["extracted"]
                  and is_candidate(rec, filters)]
    candidates.sort(key=lambda r: r.get("ts") or "")
    reason = gate_reason(args, store, candidates)
    if reason:
        log("extract", f"実行を見送りました — {reason}")
        return 0
    limit = int(getattr(args, "limit", 0) or 0)
    max_calls = int(getattr(args, "extract_max_calls", 40) or 40)
    budget = min(limit, max_calls) if limit > 0 else max_calls
    chars = int(getattr(args, "extract_input_chars", 8000) or 8000)
    done = obs_count = 0
    for rec in candidates:
        if done >= budget:
            log("extract", f"段別上限に達したため打ち切ります（{budget} 件）")
            break
        digest = record_digest(store, rec, chars)
        try:
            observations = _extract_one(args, digest)
        except LlmBlocked as e:
            elog(f"extract: {e}")
            store.save_state()
            return 1
        except LlmError as e:
            elog(f"extract: {rec['id']} の抽出に失敗（先へ進みます）: {e}")
            done += 1     # 失敗も LLM 消費なので回数に数える。レコードは未抽出のまま次回へ
            continue
        done += 1
        cli, model = _effective_agent(args)
        for i, o in enumerate(observations):
            store.append_observation({
                "id": observation_id(rec["id"], i),
                "record_id": rec["id"],
                "ts": now_iso(),
                "kind": o["kind"],
                "text": o["text"],
                "evidence": [rec["id"]],
                "extract_agent": cli,
                "extract_model": model or "",
            })
            obs_count += 1
        store.state["extracted"][rec["id"]] = 1
    store.note_run("extract")
    store.save_state()
    log("extract", f"{done} レコードを処理し、観測 {obs_count} 件を書きました")
    return 0


def _effective_agent(args) -> "tuple[str, str | None]":
    from .configfile import agent_for
    return agent_for(args, "extract")


def _extract_one(args, digest: str) -> "list[dict]":
    reply = run_llm(args, "extract", _PROMPT + digest)
    parsed = _valid_observations(parse_json_reply(reply))
    if parsed is None:
        # JSON 契約違反は 1 回だけ修復再問い合わせ（agent-flow の format_retries と同じ L2）。
        reply2 = run_llm(args, "extract", _REPAIR + reply[:400])
        parsed = _valid_observations(parse_json_reply(reply2))
        if parsed is None:
            raise LlmError("JSON 契約に 2 回一致しませんでした（この観測は捨てて先へ進みます）")
    return parsed


def _valid_observations(data) -> "list[dict] | None":
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        return None
    out = []
    for o in data["observations"]:
        if not isinstance(o, dict):
            return None
        kind = o.get("kind")
        text = o.get("text")
        if kind not in OBSERVATION_KINDS or not isinstance(text, str) or not text.strip():
            return None
        out.append({"kind": kind, "text": text.strip()})
    return out
