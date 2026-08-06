#!/usr/bin/env python3
"""定期プロンプトの `slash` プロパティのテスト。

設計: docs/designs/agent-loop-slash-property-design.md
"""
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402


class _StubSessionManager:
    """送信されたテキストを順番に記録するだけの差し替え。"""

    def __init__(self, fail_on: str = "") -> None:
        self.sent: list[str] = []
        self.fail_on = fail_on

    def sync_entries(self, entries):
        self.entries = entries

    def send_prompt(self, prompt_id, text):
        self.sent.append(text)
        return not (self.fail_on and text == self.fail_on)

    def ensure_session(self, *_a, **_k):
        return True

    def get_pane_id(self, *_a, **_k):
        return None

    def restart_pane(self, *_a, **_k):
        return None


def _scheduler(entries, fail_on: str = ""):
    mgr = _StubSessionManager(fail_on=fail_on)
    with mock.patch.object(al.PeriodicScheduler, "_set_entries", lambda *a, **k: None):
        sched = al.PeriodicScheduler(mgr, [])
    sched._set_entries(entries, allow_immediate_once=False)
    return sched, mgr


class TestNormalizeSlash(unittest.TestCase):
    def test_string_and_list_are_equivalent_shapes(self):
        self.assertEqual(al._normalize_slash("summarize-logs"), ["/summarize-logs"])
        self.assertEqual(al._normalize_slash(["a", "b"]), ["/a", "/b"])

    def test_args_are_kept_and_trimmed(self):
        self.assertEqual(al._normalize_slash(["report   --lang ja  "]), ["/report --lang ja"])

    def test_leading_slash_is_stripped(self):
        self.assertEqual(al._normalize_slash("/compact"), ["/compact"])

    def test_invalid_elements_are_dropped_not_the_entry(self):
        # 規約外（大文字・空白始まり・非文字列）は要素だけ捨て、残りは通す。
        got = al._normalize_slash(["Bad Name", "ok", 42, "", "  "], "ctx")
        self.assertEqual(got, ["/ok"])

    def test_non_sequence_is_ignored(self):
        self.assertEqual(al._normalize_slash(123), [])
        self.assertEqual(al._normalize_slash(None), [])


class TestEntryNormalization(unittest.TestCase):
    def test_slash_is_carried_on_the_entry(self):
        sched, _ = _scheduler([{
            "name": "x", "slash": ["a", "b"], "prompt": "本文", "interval_minutes": 5}])
        self.assertEqual(sched._entries[0]["slash"], ["/a", "/b"])

    def test_slash_only_entry_is_valid(self):
        """prompt 無し・event_hook 無しでも slash があれば有効（コマンドだけ定期送信）。"""
        sched, _ = _scheduler([{"name": "x", "slash": "compact", "interval_minutes": 5}])
        self.assertEqual(len(sched._entries), 1)
        self.assertEqual(sched._entries[0]["slash"], ["/compact"])

    def test_entry_without_prompt_slash_or_hook_is_dropped(self):
        sched, _ = _scheduler([{"name": "x", "interval_minutes": 5}])
        self.assertEqual(sched._entries, [])

    def test_unspecified_slash_defaults_to_empty(self):
        sched, _ = _scheduler([{"name": "x", "prompt": "本文", "interval_minutes": 5}])
        self.assertEqual(sched._entries[0]["slash"], [])


class TestDispatchOrder(unittest.TestCase):
    def _dispatch(self, entry, fail_on: str = ""):
        sched, mgr = _scheduler([], fail_on=fail_on)
        with mock.patch.object(al.time, "sleep", lambda *_a: None):
            ok = sched._dispatch_prompt(entry, None)
        return ok, mgr.sent

    def test_clear_then_slash_then_body(self):
        ok, sent = self._dispatch({
            "id": "1", "name": "x", "prompt": "本文",
            "slash": ["/a", "/b --x"], "_should_clear": True})
        self.assertTrue(ok)
        self.assertEqual(sent, ["/clear", "/a", "/b --x", "本文"])

    def test_slash_lines_are_sent_separately_not_concatenated(self):
        _ok, sent = self._dispatch({
            "id": "1", "name": "x", "prompt": "本文", "slash": ["/a"]})
        self.assertEqual(sent, ["/a", "本文"])

    def test_empty_body_is_not_sent(self):
        _ok, sent = self._dispatch({"id": "1", "name": "x", "prompt": "", "slash": ["/a"]})
        self.assertEqual(sent, ["/a"])

    def test_slash_send_failure_aborts_before_body(self):
        ok, sent = self._dispatch(
            {"id": "1", "name": "x", "prompt": "本文", "slash": ["/a", "/b"]}, fail_on="/a")
        self.assertFalse(ok)
        self.assertEqual(sent, ["/a"], "失敗した時点で止まる（本文を送らない）")

    def test_entry_without_slash_behaves_as_before(self):
        _ok, sent = self._dispatch({"id": "1", "name": "x", "prompt": "本文"})
        self.assertEqual(sent, ["本文"])


if __name__ == "__main__":
    unittest.main()
