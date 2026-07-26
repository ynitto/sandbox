"""agentcore.commands の単体テスト（指示ドロップの取り込み規約）。

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import commands  # noqa: E402


class CommandsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def drop(self, name: str, body) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def test_pending_is_name_sorted_and_skips_receipts_dir(self):
        self.drop("20260726-b.json", {"command": "board-bid"})
        self.drop("20260726-a.json", {"command": "board-bid"})
        os.makedirs(commands.receipts_dir(self.tmp))
        self.assertEqual([os.path.basename(p) for p in commands.pending(self.tmp)],
                         ["20260726-a.json", "20260726-b.json"])

    def test_pending_on_missing_dir(self):
        self.assertEqual(commands.pending(os.path.join(self.tmp, "nope")), [])

    def test_read_command(self):
        ok = self.drop("a.json", {"command": "board-bid", "id": "dg-1"})
        rec, why = commands.read_command(ok)
        self.assertEqual(rec["id"], "dg-1")
        self.assertEqual(why, "")

    def test_read_command_rejects_partial_write_and_non_object(self):
        rec, why = commands.read_command(self.drop("b.json", '{"command": "board-'))
        self.assertIsNone(rec)
        self.assertIn("JSON", why)
        rec, why = commands.read_command(self.drop("c.json", "[1, 2]"))
        self.assertIsNone(rec)
        self.assertEqual(why, "オブジェクトではない")

    def test_reject_moves_to_err_with_reason_and_original(self):
        path = self.drop("d.json", {"command": "board-bid", "id": "dg-9"})
        dest = commands.reject(path, "終端済みの公示です")
        self.assertFalse(os.path.exists(path))
        payload = json.loads(Path(dest).read_text(encoding="utf-8"))
        self.assertEqual(payload["error"], "終端済みの公示です")
        self.assertEqual(payload["command"]["id"], "dg-9")
        self.assertTrue(payload["failed_at"])

    def test_reject_keeps_unreadable_original_as_null(self):
        dest = commands.reject(self.drop("e.json", "こわれている"), "JSON 解析失敗")
        self.assertIsNone(json.loads(Path(dest).read_text(encoding="utf-8"))["command"])

    def test_write_receipt(self):
        commands.write_receipt(self.tmp, "f.json", {"action": "board-bid", "id": "dg-1"})
        rec = json.loads(Path(commands.receipts_dir(self.tmp), "f.json").read_text(encoding="utf-8"))
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["source"], "f.json")
        self.assertEqual(rec["action"], "board-bid")

    def test_prune_receipts_by_count(self):
        for i in range(5):
            commands.write_receipt(self.tmp, f"r{i}.json", {"action": "board-bid"}, keep=1000)
        commands.prune_receipts(self.tmp, keep=2)
        self.assertEqual(len(os.listdir(commands.receipts_dir(self.tmp))), 2)

    def test_prune_receipts_by_ttl(self):
        commands.write_receipt(self.tmp, "old.json", {"action": "board-bid"})
        old = os.path.join(commands.receipts_dir(self.tmp), "old.json")
        os.utime(old, (time.time() - 100000, time.time() - 100000))
        commands.prune_receipts(self.tmp, ttl_sec=3600)
        self.assertFalse(os.path.exists(old))

    def test_prune_keep_zero_removes_all(self):
        """`keep=0` は「保持しない」。`del lst[:-0]` は何も消さないので、境界を明示的に確かめる。"""
        commands.write_receipt(self.tmp, "z.json", {"action": "board-bid"}, keep=1000)
        commands.prune_receipts(self.tmp, keep=0)
        self.assertEqual(os.listdir(commands.receipts_dir(self.tmp)), [])


if __name__ == "__main__":
    unittest.main()
