from __future__ import annotations

import json
import os
import time
import unittest

from _shared import AuditTestCase, extract, distill, llm, util


class BudgetGateTests(AuditTestCase):
    def test_no_limit_no_block(self):
        self.assertIsNone(llm.budget_exceeded(self.make_args()))

    def test_token_limit_blocks(self):
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"tokens": 100, "period": "total"}, f)
        day = time.strftime("%Y%m%d", time.gmtime())
        self.write_ledger(day, [{"ts": util.now_iso(), "workload": "flow",
                                 "seconds": 10.0, "tokens_in": 90, "tokens_out": 20}])
        reason = llm.budget_exceeded(self.make_args())
        self.assertIsNotNone(reason)
        self.assertIn("[agent-error:quota]", reason)

    def test_minutes_limit_blocks(self):
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"execution_minutes": 1, "period": "total"}, f)
        day = time.strftime("%Y%m%d", time.gmtime())
        self.write_ledger(day, [{"ts": util.now_iso(), "workload": "flow", "seconds": 120.0}])
        self.assertIn("[agent-error:quota]", llm.budget_exceeded(self.make_args()) or "")

    def test_record_ledger_appends_audit_workload(self):
        llm.record_ledger(self.make_args(), 12.3, ref="extract",
                          agent_cli="ollama", model="qwen3", tokens_in=10, tokens_out=5)
        day = time.strftime("%Y%m%d", time.gmtime())
        rows = list(util.iter_jsonl(os.path.join(self.budget_dir, "ledger", f"{day}.jsonl")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["workload"], "audit")
        self.assertEqual(rows[0]["tool"], "agent-audit")


class ExtractGateTests(AuditTestCase):
    def _candidates(self, n):
        return [{"id": f"aud-{i}", "status": "failed"} for i in range(n)]

    def test_interval_gate(self):
        st = self.make_store()
        st.note_run("extract")            # たった今実行した
        args = self.make_args(force=False)
        reason = extract.gate_reason(args, st, self._candidates(100))
        self.assertIsNotNone(reason)
        self.assertIn("間隔ゲート", reason)

    def test_accumulation_gate(self):
        st = self.make_store()            # 前回実行なし → 間隔ゲートは通る
        args = self.make_args(force=False)
        reason = extract.gate_reason(args, st, self._candidates(3))
        self.assertIsNotNone(reason)
        self.assertIn("蓄積ゲート", reason)

    def test_force_bypasses_gates_only(self):
        st = self.make_store()
        st.note_run("extract")
        args = self.make_args(force=True)
        self.assertIsNone(extract.gate_reason(args, st, self._candidates(0)))

    def test_gate_zero_disables(self):
        st = self.make_store()
        st.note_run("extract")
        args = self.make_args(force=False, extract_min_interval_hours=0,
                              extract_min_records=0)
        self.assertIsNone(extract.gate_reason(args, st, []))


class DistillGateTests(AuditTestCase):
    def test_interval_and_accumulation(self):
        st = self.make_store()
        st.note_run("distill")
        args = self.make_args(force=False)
        self.assertIn("間隔ゲート", distill.gate_reason(args, st, 100) or "")
        st.state["last"]["distill"] = 0.0
        self.assertIn("蓄積ゲート", distill.gate_reason(args, st, 1) or "")
        self.assertIsNone(distill.gate_reason(args, st, 100))


class ParseJsonReplyTests(unittest.TestCase):
    def test_plain_and_fenced_and_prefixed(self):
        self.assertEqual(llm.parse_json_reply('{"a": 1}'), {"a": 1})
        self.assertEqual(llm.parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json_reply('結果は次です:\n{"a": 1}\n以上'), {"a": 1})
        self.assertIsNone(llm.parse_json_reply("json はありません"))


if __name__ == "__main__":
    unittest.main()
