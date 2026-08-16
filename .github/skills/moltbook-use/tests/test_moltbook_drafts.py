"""moltbook_drafts（K3 承認キュー）の回帰テスト — 一覧・承認・却下。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.4

GitLab へは一切繋がない（ファイルシステム操作と privacy gate の再評価だけ）。
実行: python3 -m unittest discover -s .github/skills/moltbook-use/tests
"""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MoltbookDraftsTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="moltbook-drafts-test-home-"))
        self.env = mock.patch.dict("os.environ", {"MOLTBOOK_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.drafts = _load("moltbook_drafts_test", "moltbook_drafts.py")
        self.dir = self.home / "outbox" / "drafts"
        self.dir.mkdir(parents=True)

    def _write(self, name: str, *, iid="12", author="alice", body="根拠: mem-1。回答本文。"):
        (self.dir / name).write_text(
            f"---\niid: {iid}\nauthor: {author}\ncreated: 2026-08-16\n---\n\n{body}\n",
            encoding="utf-8")

    def _args(self, **over):
        base = dict(drafts_dir=str(self.dir), json=False, file=None, reason="")
        base.update(over)
        return Namespace(**base)

    def test_list_json_includes_gate_verdict_and_body(self):
        self._write("12-alice.md")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.drafts.cmd_list(self._args(json=True))
        self.assertEqual(rc, 0)
        import json as _json
        rows = _json.loads(buf.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["iid"], "12")
        self.assertEqual(rows[0]["author"], "alice")
        self.assertIn("回答本文", rows[0]["body"])
        self.assertTrue(rows[0]["gate"]["allowed"])

    def test_list_flags_a_gate_blocked_draft(self):
        self._write("bad.md", body="このトークン glpat-abcdefghijklmnopqrst を使う")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.drafts.cmd_list(self._args(json=True))
        import json as _json
        rows = _json.loads(buf.getvalue())
        self.assertFalse(rows[0]["gate"]["allowed"])
        self.assertIn("シークレット", rows[0]["gate"]["summary"])

    def test_list_only_shows_pending_not_approved_or_sent(self):
        self._write("12-alice.md")
        (self.dir / "approved").mkdir()
        (self.dir / "approved" / "old.md").write_text("---\niid: 1\n---\n\n済\n", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.drafts.cmd_list(self._args(json=True))
        import json as _json
        rows = _json.loads(buf.getvalue())
        self.assertEqual([r["file"] for r in rows], ["12-alice.md"])

    def test_approve_moves_file_to_approved(self):
        self._write("12-alice.md")
        rc = self.drafts.cmd_approve(self._args(file="12-alice.md"))
        self.assertEqual(rc, 0)
        self.assertFalse((self.dir / "12-alice.md").exists())
        self.assertTrue((self.dir / "approved" / "12-alice.md").exists())

    def test_discard_moves_file_to_discarded_and_does_not_delete(self):
        self._write("12-alice.md")
        rc = self.drafts.cmd_discard(self._args(file="12-alice.md", reason="重複"))
        self.assertEqual(rc, 0)
        self.assertFalse((self.dir / "12-alice.md").exists())
        self.assertTrue((self.dir / "discarded" / "12-alice.md").exists(),
                        "却下は削除ではなく退避——取り消せる形にする")

    def test_approve_rejects_path_traversal(self):
        self._write("12-alice.md")
        secret = self.home / "secret.md"
        secret.write_text("outside", encoding="utf-8")
        rc = self.drafts.cmd_approve(self._args(file="../secret.md"))
        self.assertEqual(rc, 2)
        self.assertTrue(secret.exists(), "drafts の外のファイルへは触れない")

    def test_approve_missing_file_is_an_error_not_a_silent_noop(self):
        rc = self.drafts.cmd_approve(self._args(file="does-not-exist.md"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
