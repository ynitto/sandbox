"""moltbook 当番（K2）の回帰テスト — quiet 運転の下書きと reply-drafts 送信。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.3

CI には未接続（このリポジトリの `.github/skills/*` は現状テスト対象外）だが、
GitLab へは一切繋がず（GitLabClient をスタブに差し替え）ローカルで完結する。
実行: python3 -m unittest discover -s .github/skills/moltbook-use/tests
"""
from __future__ import annotations

import importlib.util
import json
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


class MoltbookReplyDraftTests(unittest.TestCase):
    """quiet 運転で自律返信がブロックされたときの下書き化（moltbook.py）。"""

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

    def test_quiet_mode_writes_draft_instead_of_posting(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(False, "reply_mode=quiet")):
            rc = self.mb.cmd_reply(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(client.notes, [], "quiet 中は GitLab へ何も送らない")
        drafts = list((self.home / "outbox" / "drafts").glob("*.md"))
        self.assertEqual(len(drafts), 1)
        text = drafts[0].read_text(encoding="utf-8")
        self.assertIn("iid: 12", text)
        self.assertIn("author: alice", text)
        self.assertIn("根拠: mem-1 を参照。", text)

    def test_budget_or_cooldown_block_stays_silent_skip_not_a_draft(self):
        """予算・深さ・クールダウンは頻度の制御であって内容の否定ではない——下書きにしない。"""
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(False, "reply_budget(3)")):
            self.mb.cmd_reply(self._args())
        self.assertEqual(client.notes, [])
        self.assertFalse((self.home / "outbox" / "drafts").exists())

    def test_active_mode_posts_normally_and_never_drafts(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(True, "ok")), \
             mock.patch.object(self.mb, "record_reply") as record:
            rc = self.mb.cmd_reply(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(client.notes, [(12, "根拠: mem-1 を参照。")])
        record.assert_called_once()
        self.assertFalse((self.home / "outbox" / "drafts").exists())

    def test_dry_run_never_touches_the_filesystem(self):
        client = _StubClient({"author": {"username": "alice"}})
        with mock.patch.object(self.mb, "_client", return_value=client), \
             mock.patch.object(self.mb, "can_reply", return_value=(False, "reply_mode=quiet")):
            self.mb.cmd_reply(self._args(dry_run=True))
        self.assertFalse((self.home / "outbox").exists())


class MoltbookSendDraftsTests(unittest.TestCase):
    """人が承認した下書き（drafts/approved/）の送信（moltbook_batch.py）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="moltbook-test-home-"))
        self.env = mock.patch.dict("os.environ", {"MOLTBOOK_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.batch = _load("moltbook_batch_duty_test", "moltbook_batch.py")
        self.approved = self.home / "outbox" / "drafts" / "approved"
        self.approved.mkdir(parents=True)

    def _write(self, name: str, *, iid="42", author="bob", body="根拠つき回答です。"):
        (self.approved / name).write_text(
            f"---\niid: {iid}\nauthor: {author}\ncreated: 2026-08-16\n---\n\n{body}\n",
            encoding="utf-8")

    def _args(self, dry_run=False):
        return Namespace(drafts_dir=str(self.approved), dry_run=dry_run)

    def test_approved_draft_is_sent_and_moved_to_sent(self):
        self._write("42-bob.md")
        client = _StubClient()
        with mock.patch.object(self.batch, "record_reply") as record:
            tally = self.batch.run_send_drafts(client, self._args())
        self.assertEqual(tally, {"sent": 1, "skipped": 0})
        self.assertEqual(client.notes, [(42, "根拠つき回答です。")])
        record.assert_called_once_with(42, "bob")
        self.assertEqual(list(self.approved.glob("*.md")), [], "送信済みは approved から消える")
        sent = list((self.approved.parent / "sent").glob("*.md"))
        self.assertEqual(len(sent), 1)

    def test_missing_iid_is_skipped_without_posting(self):
        self._write("bad.md", iid="")
        client = _StubClient()
        tally = self.batch.run_send_drafts(client, self._args())
        self.assertEqual(tally, {"sent": 0, "skipped": 1})
        self.assertEqual(client.notes, [])
        self.assertEqual(len(list(self.approved.glob("*.md"))), 1,
                         "読めなかった下書きは approved に残す（黙って消さない）")

    def test_dry_run_does_not_move_or_record(self):
        self._write("42-bob.md")
        client = _StubClient()
        with mock.patch.object(self.batch, "record_reply") as record:
            tally = self.batch.run_send_drafts(client, self._args(dry_run=True))
        self.assertEqual(tally["sent"], 1)
        record.assert_not_called()
        self.assertEqual(len(list(self.approved.glob("*.md"))), 1, "dry-run はファイルを動かさない")

    def test_no_approved_drafts_is_a_noop(self):
        client = _StubClient()
        tally = self.batch.run_send_drafts(client, self._args())
        self.assertEqual(tally, {"sent": 0, "skipped": 0})
        self.assertEqual(client.notes, [])


if __name__ == "__main__":
    unittest.main()
