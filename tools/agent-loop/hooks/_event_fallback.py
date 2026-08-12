"""Event fallback selection for GitLab and similar polling hooks."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

STATE_VERSION = 1


@dataclass(frozen=True)
class EventCandidate:
    key: str
    version: str
    sort_key: str
    payload: dict[str, Any]


@dataclass
class PendingAck:
    kind: str  # "new" | "replay" | "fallback"
    key: str | None
    version: str | None
    seen_updates: dict[str, str]
    replay_key: str | None
    at: float


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "seen": {},
        "replayed_at": {},
        "last_event_at": 0.0,
        "last_fallback_at": 0.0,
        "baseline_done": False,
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_state()
    state = empty_state()
    seen = raw.get("seen")
    if isinstance(seen, dict):
        state["seen"] = {str(k): str(v) for k, v in seen.items()}
    replayed = raw.get("replayed_at")
    if isinstance(replayed, dict):
        state["replayed_at"] = {str(k): float(v) for k, v in replayed.items()}
    for field in ("last_event_at", "last_fallback_at"):
        try:
            state[field] = float(raw.get(field) or 0.0)
        except (TypeError, ValueError):
            state[field] = 0.0
    state["baseline_done"] = bool(raw.get("baseline_done"))
    return state


def fallback_config(hook_config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = {}
    if isinstance(hook_config, dict):
        nested = hook_config.get("hook_config")
        if isinstance(nested, dict):
            cfg.update(nested)
        for key in (
            "replay_events",
            "replay_cooldown_minutes",
            "fallback_after_minutes",
            "fallback_prompt",
        ):
            if key in hook_config and key not in cfg:
                cfg[key] = hook_config[key]
    replay_events = cfg.get("replay_events")
    if replay_events is None:
        replay_events = bool(hook_config.get("event_hook_fallback")) if hook_config else False
    try:
        replay_cooldown = int(cfg.get("replay_cooldown_minutes", 60))
    except (TypeError, ValueError):
        replay_cooldown = 60
    try:
        fallback_after = int(cfg.get("fallback_after_minutes", 180))
    except (TypeError, ValueError):
        fallback_after = 180
    fallback_prompt = str(cfg.get("fallback_prompt") or "").strip()
    return {
        "replay_events": bool(replay_events),
        "replay_cooldown_minutes": max(1, replay_cooldown),
        "fallback_after_minutes": max(1, fallback_after),
        "fallback_prompt": fallback_prompt,
    }


def baseline_state(
    candidates: list[EventCandidate],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    state = empty_state()
    state["seen"] = {c.key: c.version for c in candidates}
    state["last_event_at"] = ts
    state["baseline_done"] = True
    return state


def select_event(
    candidates: list[EventCandidate],
    state: dict[str, Any],
    hook_config: dict[str, Any] | None,
    *,
    now: float | None = None,
    format_new: Callable[[EventCandidate], str | dict[str, Any]],
    format_replay: Callable[[EventCandidate], str | dict[str, Any]] | None = None,
) -> tuple[str | dict[str, Any] | None, PendingAck | None, dict[str, Any]]:
    """Return (result, pending_ack, state_after_baseline).

    On first baseline, mutates a copy with baseline fields and returns (None, None, baseline_state).
    """
    ts = float(now if now is not None else time.time())
    st = normalize_state(state)
    cfg = fallback_config(hook_config)

    if not st["baseline_done"]:
        baseline = baseline_state(candidates, now=ts)
        return None, None, baseline

    seen: dict[str, str] = dict(st["seen"])
    replayed_at: dict[str, float] = dict(st["replayed_at"])

    new_items = [
        c for c in candidates
        if c.key not in seen or seen.get(c.key) != c.version
    ]
    new_items.sort(key=lambda c: c.sort_key)
    if new_items:
        chosen = new_items[0]
        pending = PendingAck(
            kind="new",
            key=chosen.key,
            version=chosen.version,
            seen_updates={chosen.key: chosen.version},
            replay_key=None,
            at=ts,
        )
        return format_new(chosen), pending, st

    if cfg["replay_events"]:
        cooldown_sec = cfg["replay_cooldown_minutes"] * 60
        replay_items = []
        for c in candidates:
            last = replayed_at.get(c.key)
            if last is None or (ts - last) >= cooldown_sec:
                replay_items.append(c)
        replay_items.sort(key=lambda c: c.sort_key)
        if replay_items:
            chosen = replay_items[0]
            fmt = format_replay or format_new
            pending = PendingAck(
                kind="replay",
                key=chosen.key,
                version=chosen.version,
                seen_updates={chosen.key: chosen.version},
                replay_key=chosen.key,
                at=ts,
            )
            return fmt(chosen), pending, st

    last_event = float(st.get("last_event_at") or 0.0)
    if cfg["fallback_prompt"] and (ts - last_event) >= cfg["fallback_after_minutes"] * 60:
        pending = PendingAck(
            kind="fallback",
            key=None,
            version=None,
            seen_updates={},
            replay_key=None,
            at=ts,
        )
        return cfg["fallback_prompt"], pending, st

    return None, None, st


def apply_ack(state: dict[str, Any], pending: PendingAck) -> dict[str, Any]:
    st = normalize_state(state)
    st["seen"].update(pending.seen_updates)
    if pending.replay_key is not None:
        st["replayed_at"][pending.replay_key] = pending.at
    st["last_event_at"] = pending.at
    if pending.kind == "fallback":
        st["last_fallback_at"] = pending.at
    return st
