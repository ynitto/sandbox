"""実測洞察 → 型付き decision → 宣言への昇格・退役（S12、LLM 不使用）。"""
from __future__ import annotations

import json
import os
import time

from .configfile import agent_home_dir, resolve_audit_dir, resolve_budget_dir
from .stats import aggregate_ratings
from .store import Store
from .util import now_iso, parse_iso, read_json, write_json_atomic

_CONFIDENCE = {"low": 0, "medium": 1, "high": 2}


def _target_file(args, target: str) -> str:
    if target == "agent-tuning":
        return os.path.abspath(os.path.expanduser(
            getattr(args, "tuning_file", "") or os.path.join(agent_home_dir(), "tuning", "tuning.json")))
    if target == "agent-profiles":
        return os.path.abspath(os.path.expanduser(
            getattr(args, "profiles_file", "") or os.path.join(agent_home_dir(), "control", "profiles.json")))
    return os.path.join(resolve_budget_dir(args), "config.json")


def _allowed_parts(target: str, path: str, value) -> "list[str] | None":
    parts = str(path or "").split(".")
    if target == "agent-tuning" and len(parts) == 3 and parts[0] == "profiles" \
            and parts[2] in ("injections", "env") \
            and isinstance(value, list) and all(isinstance(v, str) for v in value):
        if parts[1] == "external-facing" and parts[2] == "injections" and value:
            return None
        return parts
    if target == "agent-profiles" and len(parts) == 3 and parts[0] == "tiers" \
            and parts[2] == "candidates" and isinstance(value, list) \
            and all(isinstance(v, dict) and isinstance(v.get("agent_cli"), str) for v in value):
        return parts
    prefix = "rates.per_cli."
    if target == "rates" and path.startswith(prefix) and len(path) > len(prefix) \
            and isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return ["rates", "per_cli", path[len(prefix):]]
    return None


def _get(root: dict, parts: list[str]):
    cur = root
    for key in parts[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return False, None
        cur = cur[key]
    return ((parts[-1] in cur), cur.get(parts[-1])) if isinstance(cur, dict) else (False, None)


def _set(root: dict, parts: list[str], value) -> None:
    cur = root
    for key in parts[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _delete(root: dict, parts: list[str]) -> None:
    cur = root
    for key in parts[:-1]:
        cur = cur.get(key) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return
    cur.pop(parts[-1], None)


def _rating(rows: list[dict], scope: dict) -> "dict | None":
    purpose, model = str(scope.get("purpose") or ""), str(scope.get("model") or "")
    matches = [r for r in rows if (not purpose or r.get("purpose") == purpose)
               and (not model or r.get("model") == model)]
    return sorted(matches, key=lambda r: (r.get("rank", 999), str(r.get("model"))))[0] if matches else None


def _candidate(ins: dict, existing: "dict | None", args) -> dict:
    declaration = dict(ins["declaration"])
    expires = {
        "max_age_days": 30,
        "min_pass_rate": float(getattr(args, "tune_quality_floor", 0.8)),
        "max_quality_drop": float(getattr(args, "tune_max_quality_drop", 0.05)),
        "evaluation_runs": int(getattr(args, "tune_evaluation_runs", 3)),
        **(ins.get("expires_when") or {}),
    }
    decision = dict(existing or {})
    decision.update({
        "id": f"tune-{str(ins['id']).removeprefix('ins-')}",
        "kind": "tuning-decision",
        "updated_at": now_iso(),
        "target": declaration["target"],
        "proposal": declaration,
        "scope": dict(ins.get("scope") or {}),
        "evidence": [ins["id"], *(ins.get("observation_ids") or [])],
        "occurrences": int(ins.get("occurrences") or 0),
        "confidence": str(ins.get("confidence") or "low"),
        "review": dict(ins.get("review") or {}),
        "expires_when": expires,
    })
    decision.setdefault("status", "proposed")
    decision.setdefault("created_at", now_iso())
    return decision


def _eligible(decision: dict, rating: "dict | None", args) -> "tuple[bool, str]":
    if decision["review"].get("verdict") != "supported":
        return False, "review-not-supported"
    if decision["occurrences"] < int(getattr(args, "tune_min_occurrences", 3)):
        return False, "not-reproduced"
    required = _CONFIDENCE.get(str(getattr(args, "tune_min_confidence", "high")), 2)
    if _CONFIDENCE.get(decision["confidence"], 0) < required:
        return False, "confidence-low"
    if decision["target"] != "rates":
        if not rating or int(rating.get("outcome_runs") or 0) < int(getattr(args, "tune_min_outcomes", 3)):
            return False, "quality-sample-small"
        if rating.get("pass_rate") is None or float(rating["pass_rate"]) < float(
                decision["expires_when"]["min_pass_rate"]):
            return False, "quality-floor"
    return True, ""


def _revision_of(root) -> int:
    try:
        return int((root or {}).get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def _write_declaration(path: str, root: dict, base_revision: int, updated_by: str) -> bool:
    """宣言ファイルを書き戻す。`revision` を持つ契約は書く直前に照合する。

    agent-tuning の書き手は人・dashboard・agent-loop・ここの 4 つで、全員が読んで直して
    全体を置き換える。照合しないと後勝ちで前の変更が消え、revision は両者とも +1 するので
    消えたことにも気づけない。`revision` を持たない契約（budget config 等）は従来どおり。
    """
    if base_revision is not None:
        if _revision_of(read_json(path)) != base_revision:
            return False
        root["revision"] = base_revision + 1
    root["updated_at"] = now_iso()
    root["updated_by"] = updated_by
    write_json_atomic(path, root)
    return True


def _promote(decision: dict, rating: "dict | None", args) -> "tuple[bool, str]":
    proposal = decision["proposal"]
    parts = _allowed_parts(decision["target"], proposal["path"], proposal["value"])
    if not parts:
        return False, "path-or-value-not-allowed"
    path = _target_file(args, decision["target"])
    root = read_json(path)
    if not isinstance(root, dict):
        return False, "target-missing-or-invalid"
    existed, previous = _get(root, parts)
    base = _revision_of(root) if decision["target"] == "agent-tuning" else None
    _set(root, parts, proposal["value"])
    if not _write_declaration(path, root, base, "agent-audit"):
        return False, "target-changed-concurrently"
    decision.update({
        "status": "promoted", "promoted_at": now_iso(),
        "baseline": dict(rating or {}),
        "applied": {"file": path, "parts": parts, "value": proposal["value"],
                    "previous_exists": existed, "previous": previous},
    })
    return True, ""


def _retirement_reason(decision: dict, rating: "dict | None", now: float) -> str:
    expires = decision.get("expires_when") or {}
    promoted = parse_iso(decision.get("promoted_at")) or now
    max_days = float(expires.get("max_age_days") or 0)
    if max_days and now - promoted >= max_days * 86400:
        return "expired"
    baseline = decision.get("baseline") or {}
    if not rating or baseline.get("pass_rate") is None or rating.get("pass_rate") is None:
        return ""
    new_runs = int(rating.get("outcome_runs") or 0) - int(baseline.get("outcome_runs") or 0)
    if new_runs < int(expires.get("evaluation_runs") or 1):
        return ""
    current = float(rating["pass_rate"])
    if current < float(expires.get("min_pass_rate") or 0):
        return "quality-floor"
    if float(baseline["pass_rate"]) - current > float(expires.get("max_quality_drop") or 0):
        return "quality-regression"
    return ""


def _retire(decision: dict, reason: str) -> None:
    applied = decision.get("applied") or {}
    path, parts = applied.get("file"), applied.get("parts")
    root = read_json(path) if path else None
    if isinstance(root, dict) and isinstance(parts, list):
        exists, current = _get(root, parts)
        if exists and current == applied.get("value"):
            if applied.get("previous_exists"):
                _set(root, parts, applied.get("previous"))
            else:
                _delete(root, parts)
            base = _revision_of(root) if decision.get("target") == "agent-tuning" else None
            if not _write_declaration(path, root, base, "agent-audit-retire"):
                reason += ":write-conflict"
        elif exists:
            reason += ":superseded"
    decision.update({"status": "retired", "retired_at": now_iso(), "retire_reason": reason})


def run_tuning(args, store: Store, *, apply: bool, now: "float | None" = None) -> list[dict]:
    period = getattr(args, "period", None) or getattr(args, "tune_period", "month")
    rows = aggregate_ratings(args, store, period)
    existing = {d.get("id"): d for d in store.iter_decisions()}
    decisions = dict(existing)
    for ins in store.iter_insights():
        if isinstance(ins.get("declaration"), dict):
            did = f"tune-{str(ins['id']).removeprefix('ins-')}"
            if (existing.get(did) or {}).get("status") != "retired":
                decisions[did] = _candidate(ins, existing.get(did), args)

    ts = time.time() if now is None else float(now)
    for decision in decisions.values():
        if decision.get("status") == "promoted":
            reason = _retirement_reason(decision, _rating(rows, decision.get("scope") or {}), ts)
            if reason:
                _retire(decision, reason)

    if apply:
        per_run = int(getattr(args, "tune_max_promotions_per_run", 1))
        total_limit = int(getattr(args, "tune_max_total_promotions", 20))
        total = int(store.state.get("tuning_promotions") or 0)
        promoted = 0
        for decision in sorted(decisions.values(), key=lambda d: d["id"]):
            if decision.get("status") != "proposed":
                continue
            # 上限で止めたことは候補側にも残す。生涯上限（tuning_promotions）は単調増加で
            # リセット手段が無いので、黙って止まると「なぜ昇格しないのか」が誰にも見えない。
            if total >= total_limit:
                decision["blocked_reason"] = "promotion-budget-exhausted"
                continue
            if promoted >= per_run:
                decision["blocked_reason"] = "promotion-quota-this-run"
                continue
            rating = _rating(rows, decision.get("scope") or {})
            ok, reason = _eligible(decision, rating, args)
            if not ok:
                decision["blocked_reason"] = reason
                continue
            ok, reason = _promote(decision, rating, args)
            if ok:
                decision.pop("blocked_reason", None)
                promoted += 1
                total += 1
            else:
                decision["blocked_reason"] = reason
        store.state["tuning_promotions"] = total

    for decision in decisions.values():
        store.write_decision(decision)
    store.note_run("tune", ts)
    store.save_state()
    return sorted(decisions.values(), key=lambda d: d["id"])


def cmd_tune(args) -> int:
    store = Store(resolve_audit_dir(args))
    decisions = run_tuning(args, store, apply=bool(getattr(args, "apply", False)))
    if getattr(args, "json", False):
        print(json.dumps(decisions, ensure_ascii=False, indent=1))
    else:
        counts = {s: sum(d.get("status") == s for d in decisions)
                  for s in ("proposed", "promoted", "retired")}
        used = int(store.state.get("tuning_promotions") or 0)
        limit = int(getattr(args, "tune_max_total_promotions", 20))
        print("tuning decisions: " + " ".join(f"{k}={v}" for k, v in counts.items())
              + f" / 昇格の生涯上限 {used}/{limit}")
        blocked = {}
        for d in decisions:
            if d.get("status") == "proposed" and d.get("blocked_reason"):
                blocked[d["blocked_reason"]] = blocked.get(d["blocked_reason"], 0) + 1
        for reason, n in sorted(blocked.items()):
            print(f"  保留 {reason}: {n}")
    return 0
