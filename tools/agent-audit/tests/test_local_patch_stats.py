# 局所修正の適格（local_patch_blockers）の集計＝保留の解除条件（計画 2026-08-22 §4.3 A2）。
#
# 08-22 は「拒否は配線せず頻度を数える」と決めたが、数える実装が無いまま 1 週間が過ぎ、
# 解除条件が原理的に発火しない状態だった（棚卸し 2026-08-29 §2.1）。ここが縛るのは
# 「しきい値で発火すること」と「0/0 を発火しないこと」の 2 つである。
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from _shared import AuditTestCase, stats  # noqa: E402


def receipt(node: str, *, status: str, blockers=None, cli: str = "aider") -> dict:
    rec = {"id": f"flow-result:{node}", "ts": "2026-08-29T00:00:00Z",
           "kind": "result", "source": "flow-bus", "tool": "agent-flow",
           "workload": "flow", "ref": f"run/{node}", "purpose": "work",
           "agent_cli": cli, "model": "gemma4:e4b", "status": status}
    if blockers:
        rec["local_patch_blockers"] = list(blockers)
    return rec


class LocalPatchBlockerStatsTests(AuditTestCase):
    def _stats(self, records, **over):
        store = self.make_store()
        for rec in records:
            store.append_record(rec)
        return stats.local_patch_blocker_stats(store, "all", **over)

    def test_no_receipts_does_not_read_as_healthy(self):
        # 0/0 は「不適格な割り当ては起きていない」ではなく「測っていない」。
        result = self._stats([])
        self.assertEqual(result["samples"], 0)
        self.assertFalse(result["reevaluate"])

    def test_done_nodes_are_not_counted_as_escalated(self):
        # 分母は「done しなかったノード」。通ったノードは上位へ回っていない。
        result = self._stats([receipt("n1", status="done", blockers=["書込 scope"])])
        self.assertEqual(result["nodes"], 1)
        self.assertEqual(result["samples"], 0)
        self.assertFalse(result["reevaluate"])

    def test_threshold_fires_at_one_third(self):
        records = [receipt("n1", status="failed", blockers=["書込 scope は 1 ファイル"]),
                   receipt("n2", status="failed"),
                   receipt("n3", status="failed")]
        result = self._stats(records)
        self.assertEqual((result["with_blockers"], result["samples"]), (1, 3))
        self.assertTrue(result["reevaluate"], "1/3 で発火する（08-22 の再評価条件）")
        self.assertEqual(result["blockers"], {"書込 scope は 1 ファイル": 1})

    def test_below_threshold_does_not_fire(self):
        records = [receipt("n1", status="failed", blockers=["x"])] + [
            receipt(f"n{i}", status="failed") for i in range(2, 6)]
        result = self._stats(records)
        self.assertEqual((result["with_blockers"], result["samples"]), (1, 5))
        self.assertFalse(result["reevaluate"])

    def test_other_agents_are_not_counted(self):
        # 対象は局所修正 worker だけ。他の候補の失敗を混ぜると比率が薄まる。
        result = self._stats([receipt("n1", status="failed", blockers=["x"], cli="claude")])
        self.assertEqual(result["nodes"], 0)

    def test_cmd_stats_reports_the_trigger(self):
        store = self.make_store()
        for i in range(3):
            store.append_record(receipt(f"n{i}", status="failed",
                                        blockers=["書込 scope は 1 ファイル"] if not i else None))
        args = self.make_args(period="all", json=True)
        from contextlib import redirect_stdout
        import io
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(stats.cmd_stats(args), 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["local_patch"]["reevaluate"])


if __name__ == "__main__":
    unittest.main()
