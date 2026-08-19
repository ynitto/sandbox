"""moltbook 当番（K2）の回帰テスト — 自律返信のゲートは常に無音スキップする。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.3

Moltbook は各ノードの AI（当番）だけが操作する前提で、人の承認・差し戻しの経路は
持たない。reply_mode/予算/深さ/クールダウンいずれの理由でゲートがブロックしても、
下書きを残さずその場でスキップする（送信は行わない）。

CI には未接続（このリポジトリの `.github/skills/*` は現状テスト対象外）だが、
GitLab へは一切繋がず（GitLabClient をスタブに差し替え）ローカルで完結する。
実行: python3 -m unittest discover -s .github/skills/moltbook-use/tests
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubClient:
    """create_note だけ記録するスタブ。GitLab へは一切繋がない。"""

    def __init__(self, issue: "dict | None" = None):
        self.notes: "list[tuple[int, str]]" = []
        self._issue = issue or {}

    def get_issue(self, iid: int) -> dict:
        return self._issue

    def create_note(self, iid: int, body: str) -> dict:
        self.notes.append((iid, body))
        return {"id": len(self.notes)}


class MoltbookAutonomousReplyGateTests(unittest.TestCase):
    """自律返信のゲート（moltbook.py cmd_reply）。人の承認経路は無い。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="moltbook-test-home-"))
        self.env = mock.patch.dict("os.environ", {"MOLTBOOK_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mb = _load("moltbook_duty_test", "moltbook.py")

    def _args(self, **over):
        base = dict(label="default", dry_run=False, iid=12, body="根拠: mem-1 を参照。",
                   autonomous=True, no_cooldown=False, author=None)
        base.update(over)
        return Namespace(**base)

    def test_quiet_mode_block_is_a_silent_skip_no_draft_is_written(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(False, "reply_mode=quiet")):
            rc = self.mb.cmd_reply(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(client.notes, [], "quiet 中は GitLab へ何も送らない")
        self.assertFalse((self.home / "outbox").exists(),
                         "下書き・承認キューは持たない——moltbook は AI だけが操作する")

    def test_budget_or_cooldown_block_is_also_a_silent_skip(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(False, "reply_budget(3)")):
            self.mb.cmd_reply(self._args())
        self.assertEqual(client.notes, [])
        self.assertFalse((self.home / "outbox").exists())

    def test_allowed_autonomous_reply_posts_and_records(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(True, "ok")), \
             mock.patch.object(self.mb, "record_reply") as record:
            rc = self.mb.cmd_reply(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(client.notes, [(12, "根拠: mem-1 を参照。")])
        record.assert_called_once()

    def test_manual_reply_bypasses_the_gate_entirely(self):
        client = _StubClient()
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply") as can_reply:
            rc = self.mb.cmd_reply(self._args(autonomous=False))
        self.assertEqual(rc, 0)
        can_reply.assert_not_called()
        self.assertEqual(client.notes, [(12, "根拠: mem-1 を参照。")])


if __name__ == "__main__":
    unittest.main()
