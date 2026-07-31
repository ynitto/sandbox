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


class DebounceTests(unittest.TestCase):
    """書きかけ猶予と順序保存（P1-4）。

    スキーマは書き手として人を認めている（手置き）。手で置いたファイルは原子的とは
    限らないので、読めないうちは猶予を与えて次の巡回で読み直す——即 `.err` にすると
    人の指示が失われる。**読める指示は猶予しない**（読める承認まで先送りすると、
    起こしたパスで承認が取り込まれない）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def drop(self, name: str, body) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def age(self, name: str, seconds: float) -> None:
        p = os.path.join(self.tmp, name)
        old = time.time() - seconds
        os.utime(p, (old, old))

    def test_unreadable_file_is_deferred_within_the_grace(self):
        self.drop("a.json", '{"command": "board-')
        self.assertEqual(commands.pending(self.tmp, debounce_sec=3.0), [])
        self.age("a.json", 10)
        self.assertEqual(len(commands.pending(self.tmp, debounce_sec=3.0)), 1)

    def test_readable_file_is_never_deferred(self):
        self.drop("a.json", {"command": "board-bid", "id": "dg-1"})
        self.assertEqual(len(commands.pending(self.tmp, debounce_sec=3.0)), 1)

    def test_stop_at_deferred_holds_back_the_rest(self):
        # 「入札 → 中止」の順序が壊れると、中止済みの板へ入札を書くことになる。
        self.drop("01-bid.json", '{"command": "board-')
        self.drop("02-cancel.json", {"command": "board-cancel", "id": "dg-1"})
        self.assertEqual(commands.pending(self.tmp, debounce_sec=3.0, stop_at_deferred=True), [])
        # 順序を持たないスコープ（プロジェクト配下）は飛ばして先へ進む
        rest = commands.pending(self.tmp, debounce_sec=3.0)
        self.assertEqual([os.path.basename(p) for p in rest], ["02-cancel.json"])


class RejectedCleanupTests(unittest.TestCase):
    """失敗退避（`.err`）の掃除（P1-4）。`.err` は書き手の失敗バナーの根拠なので、
    消えるのは「同じ id の指示が通ったとき」と「期限切れ」だけ。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def reject(self, name: str, rec: dict) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return commands.reject(path, "だめでした")

    def test_clear_rejected_matches_by_id(self):
        self.reject("a.json", {"command": "board-bid", "id": "dg-1"})
        self.reject("b.json", {"command": "board-bid", "id": "dg-2"})
        self.assertEqual(commands.clear_rejected(self.tmp, "dg-1"), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a.json.err")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "b.json.err")),
                        "別の対象の失敗まで消すと、失敗が誰にも見えなくなる")

    def test_clear_rejected_reads_the_legacy_shape(self):
        # 旧実装は元の指示 JSON をそのまま改名した（{"command": "approve", "id": …}）。
        with open(os.path.join(self.tmp, "old.json.err"), "w", encoding="utf-8") as f:
            json.dump({"command": "approve", "id": "T1"}, f)
        self.assertEqual(commands.clear_rejected(self.tmp, "T1"), 1)

    def test_clear_rejected_ignores_empty_id(self):
        self.reject("a.json", {"command": "board-bid", "id": "dg-1"})
        self.assertEqual(commands.clear_rejected(self.tmp, ""), 0)

    def test_prune_rejected_by_ttl(self):
        err = self.reject("a.json", {"command": "board-bid", "id": "dg-1"})
        os.utime(err, (time.time() - 100000, time.time() - 100000))
        self.assertEqual(commands.prune_rejected(self.tmp, ttl_sec=3600), 1)
        self.assertFalse(os.path.exists(err))

    def test_prune_rejected_by_count(self):
        for i in range(5):
            self.reject(f"a{i}.json", {"command": "board-bid", "id": f"dg-{i}"})
        self.assertEqual(commands.prune_rejected(self.tmp, keep=2), 3)


if __name__ == "__main__":
    unittest.main()
