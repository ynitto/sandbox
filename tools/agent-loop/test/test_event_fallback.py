#!/usr/bin/env python3
"""Event fallback selection tests."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from _event_fallback import (  # noqa: E402
    EventCandidate,
    PendingAck,
    apply_ack,
    baseline_state,
    empty_state,
    select_event,
)


def _c(key: str, version: str) -> EventCandidate:
    return EventCandidate(key=key, version=version, sort_key=version, payload={"key": key})


class EventFallbackTests(unittest.TestCase):
    def test_first_baseline_returns_none(self):
        candidates = [_c("p:issue:1", "t1"), _c("p:issue:2", "t2")]
        result, pending, updated = select_event(
            candidates,
            empty_state(),
            {"event_hook_config": {"replay_events": True, "fallback_prompt": "fb"}},
            now=100.0,
            format_new=lambda c: f"new:{c.key}",
        )
        self.assertIsNone(result)
        self.assertIsNone(pending)
        self.assertTrue(updated["baseline_done"])
        self.assertEqual(updated["seen"]["p:issue:1"], "t1")

    def test_new_before_replay_and_fallback(self):
        state = baseline_state([_c("p:issue:1", "t1")], now=50.0)
        candidates = [_c("p:issue:1", "t2"), _c("p:issue:2", "t0")]
        cfg = {
            "event_hook_config": {
                "replay_events": True,
                "replay_cooldown_minutes": 60,
                "fallback_after_minutes": 1,
                "fallback_prompt": "fallback",
            }
        }
        result, pending, _ = select_event(
            candidates,
            state,
            cfg,
            now=100.0,
            format_new=lambda c: f"new:{c.key}",
            format_replay=lambda c: f"replay:{c.key}",
        )
        self.assertEqual(result, "new:p:issue:2")
        self.assertEqual(pending.kind, "new")

    def test_replay_respects_cooldown(self):
        state = baseline_state([_c("p:issue:1", "t1")], now=0.0)
        state["replayed_at"] = {"p:issue:1": 100.0}
        cfg = {"event_hook_config": {"replay_events": True, "replay_cooldown_minutes": 60}}
        result, pending, _ = select_event(
            [_c("p:issue:1", "t1")],
            state,
            cfg,
            now=120.0,
            format_new=lambda c: f"new:{c.key}",
        )
        self.assertIsNone(result)
        self.assertIsNone(pending)

        result2, pending2, _ = select_event(
            [_c("p:issue:1", "t1")],
            state,
            cfg,
            now=3700.0,
            format_new=lambda c: f"new:{c.key}",
            format_replay=lambda c: f"replay:{c.key}",
        )
        self.assertEqual(result2, "replay:p:issue:1")
        self.assertEqual(pending2.kind, "replay")

    def test_fallback_after_idle(self):
        state = baseline_state([_c("p:issue:1", "t1")], now=0.0)
        cfg = {
            "event_hook_config": {
                "replay_events": False,
                "fallback_after_minutes": 30,
                "fallback_prompt": "idle prompt",
            }
        }
        result, pending, _ = select_event(
            [_c("p:issue:1", "t1")],
            state,
            cfg,
            now=2000.0,
            format_new=lambda c: c.key,
        )
        self.assertEqual(result, "idle prompt")
        self.assertEqual(pending.kind, "fallback")

    def test_ack_commits_seen_not_before(self):
        state = baseline_state([_c("p:issue:1", "t1")], now=0.0)
        pending = PendingAck(
            kind="new",
            key="p:issue:2",
            version="t2",
            seen_updates={"p:issue:2": "t2"},
            replay_key=None,
            at=200.0,
        )
        self.assertNotIn("p:issue:2", state["seen"])
        updated = apply_ack(state, pending)
        self.assertEqual(updated["seen"]["p:issue:2"], "t2")
        self.assertEqual(updated["last_event_at"], 200.0)


if __name__ == "__main__":
    unittest.main()
