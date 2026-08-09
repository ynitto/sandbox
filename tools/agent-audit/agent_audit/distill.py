"""distill — 観測クラスタ → 洞察（reduce・中〜強モデル。設計 §6.3）。

クラスタリングは stdlib だけの決定的処理（同じ観測集合からは必ず同じクラスタ）。
distill はクラスタ単位でだけ走り、単発の観測を洞察にしない。クラスタが育ったら
同じ洞察 id を改訂する（1 洞察 1 ファイルにした理由）。
"""
from __future__ import annotations

import hashlib
import re
import time

from .configfile import resolve_audit_dir
from .llm import LlmBlocked, LlmError, parse_json_reply, run_llm
from .store import Store
from .util import elog, log, now_iso

INSIGHT_KINDS = ("rule-candidate", "skill-improvement", "config-fix", "usage-optimization")
SIMILARITY_THRESHOLD = 0.5     # overlap 係数（|A∩B| / min(|A|,|B|)）の合流閾値

_WORD_RE = re.compile(r"[A-Za-z0-9_.-]{2,}")
_CJK_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]{2,}")

_PROMPT = """あなたはエージェント運用の知見をまとめる係です。以下は同種と判定された
観測の集まりです。これらを 1 つの一般化された洞察に畳んでください。

出力は次の JSON だけ（説明文・コードフェンス不要）:
{"statement": "<一般化した知見（日本語・1〜2 文）>",
 "kind": "rule-candidate|skill-improvement|config-fix|usage-optimization",
 "suggested_action": "<rules.md 候補文 / skill の修正案 / 設定変更案（具体的に）>",
 "confidence": "low|medium|high",
 "scope": {"purpose": "<仕事種別>", "model": "<対象モデル>"},
 "expires_when": {"max_age_days": 30, "min_pass_rate": 0.8,
                    "max_quality_drop": 0.05, "evaluation_runs": 3},
 "declaration": null | {"target": "agent-tuning|agent-profiles|rates", "op": "set",
   "path": "<許可された宣言パス>", "value": "<設定値>"}}

declaration は実測から安全に設定へ還せる場合だけ付け、そうでなければ null にしてください。
許可パスは agent-tuning=profiles.<name>.injections|env、
agent-profiles=tiers.<name>.candidates、rates=rates.per_cli.<cli[:model]> だけです。

観測:
"""

_REVIEW_PROMPT = """以下の洞察を、根拠となった観測と突き合わせて検証してください。
出力は次の JSON だけ:
{"verdict": "supported|weak|refuted", "reason": "<1 文>"}

洞察:
"""


def _tokens(text: str) -> "frozenset[str]":
    """英数語 + CJK 文字 bigram。形態素解析は使わない（決定性と stdlib 縛りを優先）。"""
    toks = {w.lower() for w in _WORD_RE.findall(text or "")}
    for run in _CJK_RE.findall(text or ""):
        toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return frozenset(toks)


def cluster_observations(observations: "list[dict]") -> "list[dict]":
    """貪欲クラスタリング（決定的）: 観測 id 順に走査し、同じ kind でトークンの
    overlap 係数が閾値以上の最初のクラスタへ合流、無ければ新設。
    cluster_id は kind + 最小観測 id のハッシュ——クラスタが育っても概ね安定し、
    同じ洞察の改訂として扱える。"""
    clusters: "list[dict]" = []
    for obs in sorted(observations, key=lambda o: o.get("id") or ""):
        toks = _tokens(obs.get("text") or "")
        placed = False
        for c in clusters:
            if c["kind"] != obs.get("kind"):
                continue
            denom = min(len(c["tokens"]), len(toks))
            if denom and len(c["tokens"] & toks) / denom >= SIMILARITY_THRESHOLD:
                c["observations"].append(obs)
                c["tokens"] = c["tokens"] | toks
                placed = True
                break
        if not placed:
            clusters.append({"kind": obs.get("kind"), "tokens": toks, "observations": [obs]})
    for c in clusters:
        first = min(o["id"] for o in c["observations"])
        h = hashlib.sha256(f"{c['kind']}::{first}".encode("utf-8")).hexdigest()
        c["cluster_id"] = f"ins-{h[:16]}"
    return clusters


def gate_reason(args, store: Store, new_obs_total: int) -> "str | None":
    if getattr(args, "force", False):
        return None
    interval_h = float(getattr(args, "distill_min_interval_hours", 24.0) or 0.0)
    if interval_h > 0:
        elapsed = time.time() - store.last_run("distill")
        if elapsed < interval_h * 3600.0:
            return (f"間隔ゲート: 前回 distill から {elapsed / 3600.0:.1f}h "
                    f"< {interval_h}h（--force で強制）")
    min_new = int(getattr(args, "distill_min_new_observations", 5) or 0)
    if min_new > 0 and new_obs_total < min_new:
        return (f"蓄積ゲート: 新規観測 {new_obs_total} 件 < {min_new} 件（--force で強制）")
    return None


def cmd_distill(args) -> int:
    store = Store(resolve_audit_dir(args))
    observations = list(store.iter_observations())
    if not observations:
        log("distill", "観測がまだありません（まず agent-audit extract を実行してください）")
        return 0
    clusters = cluster_observations(observations)
    known = store.state.setdefault("clusters", {})     # cluster_id → 前回 distill 時の観測数
    min_occ = int(getattr(args, "distill_min_occurrences", 2) or 2)
    targets = []
    new_obs_total = 0
    for c in clusters:
        size = len(c["observations"])
        prev = int(known.get(c["cluster_id"]) or 0)
        if size > prev:
            new_obs_total += size - prev
        if size >= min_occ and size > prev:
            targets.append(c)
    reason = gate_reason(args, store, new_obs_total)
    if reason:
        log("distill", f"実行を見送りました — {reason}")
        return 0
    limit = int(getattr(args, "limit", 0) or 0)
    max_calls = int(getattr(args, "distill_max_calls", 10) or 10)
    budget = min(limit, max_calls) if limit > 0 else max_calls
    made = 0
    for c in sorted(targets, key=lambda c: (-len(c["observations"]), c["cluster_id"])):
        if made >= budget:
            log("distill", f"段別上限に達したため打ち切ります（{budget} 件）")
            break
        try:
            ins = _distill_one(args, c)
        except LlmBlocked as e:
            elog(f"distill: {e}")
            store.save_state()
            return 1
        except LlmError as e:
            elog(f"distill: {c['cluster_id']} の蒸留に失敗（先へ進みます）: {e}")
            made += 1
            continue
        made += 1
        if getattr(args, "review", False):
            ins["review"] = _review_one(args, ins, c)
        store.write_insight(ins)
        known[c["cluster_id"]] = len(c["observations"])
    store.note_run("distill")
    store.save_state()
    log("distill", f"クラスタ {made} 件を処理しました（洞察は insights/ へ）")
    return 0


def _distill_one(args, cluster: dict) -> dict:
    lines = "\n".join(f"- ({o['kind']}) {o['text']}" for o in cluster["observations"])
    reply = run_llm(args, "distill", _PROMPT + lines)
    data = parse_json_reply(reply)
    if not _valid_insight(data):
        reply2 = run_llm(args, "distill",
                         "先ほどの出力は JSON 契約に一致しませんでした。契約どおりの JSON だけを"
                         "出力し直してください。\n\n" + _PROMPT + lines)
        data = parse_json_reply(reply2)
        if not _valid_insight(data):
            raise LlmError("JSON 契約に 2 回一致しませんでした")
    obs_ids = sorted(o["id"] for o in cluster["observations"])
    return {
        "id": cluster["cluster_id"],
        "ts": now_iso(),
        "statement": data["statement"].strip(),
        "kind": data["kind"],
        "scope": data.get("scope") if isinstance(data.get("scope"), dict) else {},
        "observation_ids": obs_ids,
        "occurrences": len(obs_ids),
        "suggested_action": str(data.get("suggested_action") or "").strip(),
        "confidence": data.get("confidence") if data.get("confidence") in
                      ("low", "medium", "high") else "low",
        "expires_when": (data.get("expires_when")
                         if isinstance(data.get("expires_when"), dict) else {}),
        "declaration": (_valid_declaration(data.get("declaration"))
                        and data.get("declaration") or None),
        "review": None,
        "exported": False,
    }


def _valid_insight(data) -> bool:
    return (isinstance(data, dict)
            and isinstance(data.get("statement"), str) and data["statement"].strip()
            and data.get("kind") in INSIGHT_KINDS)


def _valid_declaration(value) -> bool:
    return (isinstance(value, dict)
            and value.get("target") in ("agent-tuning", "agent-profiles", "rates")
            and value.get("op") == "set"
            and isinstance(value.get("path"), str) and value["path"].strip()
            and "value" in value)


def _review_one(args, ins: dict, cluster: dict) -> "dict | None":
    lines = "\n".join(f"- {o['text']}" for o in cluster["observations"])
    try:
        reply = run_llm(args, "review",
                        _REVIEW_PROMPT + ins["statement"] + "\n\n観測:\n" + lines)
    except (LlmBlocked, LlmError) as e:
        elog(f"review: {ins['id']} の検証に失敗（未検証のまま残します）: {e}")
        return None
    data = parse_json_reply(reply)
    if isinstance(data, dict) and data.get("verdict") in ("supported", "weak", "refuted"):
        return {"verdict": data["verdict"], "reason": str(data.get("reason") or "")}
    return None
