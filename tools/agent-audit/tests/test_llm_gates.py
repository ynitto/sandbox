from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from _shared import AuditTestCase, extract, distill, llm, util


class BudgetGateTests(AuditTestCase):
    def test_audit_resolves_declared_variant_without_cli_specific_branch(self):
        agentcli = mock.Mock()
        agentcli.resolve_variant.return_value = {
            "agent_cli": "base-json", "default_model": "structured-model"}

        self.assertEqual(
            llm._execution_cli("base", None, "extract", agentcli),
            ("base-json", "structured-model"))
        agentcli.resolve_variant.assert_called_once_with("base", "extract", None)

        # 明示モデルは変種の既定で上書きしない（設計 2026-08-27 G4）。
        agentcli.reset_mock()
        self.assertEqual(llm._execution_cli("base", "chosen", "extract", agentcli),
                         ("base-json", "chosen"))

        # 申告が無い用途は元のまま。**audit は許可リストを持たない**——引くかどうかでは
        # なく、申告があるかどうかで決まる（設計 2026-08-27 §3.3 / G2）。
        agentcli.reset_mock()
        agentcli.resolve_variant.return_value = None
        self.assertEqual(llm._execution_cli("base", "chosen", "distill", agentcli),
                         ("base", "chosen"))
        agentcli.resolve_variant.assert_called_once_with("base", "distill", None)

    def test_a_by_purpose_policy_never_reaches_audit(self):
        """用途別の順位表（`selection_policy.by_purpose`）は audit へ届かない。

        調停規則（設計 2026-08-27 G4）は「用途別の実測で選ばれたモデルを変種の既定で
        上書きしない」だが、守れるのは**その決定を受け取る経路だけ**である。audit は
        version 2 の `selection_policy` を読まず legacy の `agents:` しか見ないので、
        by_purpose のモデルが「明示でないモデル」として紛れ込むことがない。

        縛るのは、将来 audit へ `selection_policy` を教えるとき **`by_purpose=` を
        一緒に渡さないと静かに壊れる**という一点である。
        """
        control_dir = os.path.join(self.tmp, "control")
        os.makedirs(control_dir, exist_ok=True)
        old = os.environ.get("AGENT_CONTROL_DIR")
        os.environ["AGENT_CONTROL_DIR"] = control_dir
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_CONTROL_DIR", old)
                        if old is not None else os.environ.pop("AGENT_CONTROL_DIR", None))
        with open(os.path.join(control_dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({
                "version": 2,
                "workloads": {"audit": {"selection_policy": {
                    "strategy": "balanced", "retry_limit": 1, "no_candidate": "park",
                    "candidates": [{"agent_cli": "ollama", "model": "gemma4:e4b", "rank": 1}],
                    "by_purpose": {"extract": {"operations": ["bounded-review"],
                                               "candidates": [{"agent_cli": "ollama",
                                                               "model": "gemma4:12b",
                                                               "rank": 1}]}},
                }}},
            }, f)
        self.assertEqual(llm.control_overrides(self.make_args(), "extract"), (None, None))

    def test_control_uses_shared_dir_and_purpose_precedence(self):
        control_dir = os.path.join(self.tmp, "control")
        os.makedirs(control_dir, exist_ok=True)
        old = os.environ.get("AGENT_CONTROL_DIR")
        os.environ["AGENT_CONTROL_DIR"] = control_dir
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_CONTROL_DIR", old)
                        if old is not None else os.environ.pop("AGENT_CONTROL_DIR", None))
        with open(os.path.join(control_dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({
                "defaults": {"agent_cli": "codex", "model": "default"},
                "workloads": {"audit": {
                    "agent_cli": "claude", "model": "sonnet",
                    "agents": {"extract": {"model": "haiku"}},
                }},
            }, f)
        self.assertEqual(llm.control_overrides(self.make_args(), "extract"), ("claude", "haiku"))

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

    def test_audit_workload_limit_blocks_without_exhausting_global_limit(self):
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "tokens": 1000, "period": "total",
                "allocation": {"workloads": {"audit": {"max_tokens": 100}}},
            }, f)
        day = time.strftime("%Y%m%d", time.gmtime())
        self.write_ledger(day, [{"ts": util.now_iso(), "workload": "audit",
                                 "seconds": 1.0, "tokens_in": 80, "tokens_out": 30}])
        self.assertIn("[agent-error:quota]", llm.budget_exceeded(self.make_args()) or "")

    def test_degrade_state_and_status_use_audit_contract(self):
        control_dir = os.path.join(self.tmp, "control")
        os.makedirs(control_dir, exist_ok=True)
        old = os.environ.get("AGENT_CONTROL_DIR")
        os.environ["AGENT_CONTROL_DIR"] = control_dir
        self.addCleanup(lambda: os.environ.__setitem__("AGENT_CONTROL_DIR", old)
                        if old is not None else os.environ.pop("AGENT_CONTROL_DIR", None))
        with open(os.path.join(control_dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump({"revision": 7, "workloads": {"audit": {
                "tier": "medium", "selection_source": "control-workload",
                "degraded": {"agent_cli": "ollama", "model": "small"},
            }}}, f)
        with open(os.path.join(self.budget_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"period": "total", "allocation": {"workloads": {
                "audit": {"max_tokens": 100, "on_exhausted": "degrade"},
            }}}, f)
        day = time.strftime("%Y%m%d", time.gmtime())
        self.write_ledger(day, [{"ts": util.now_iso(), "workload": "audit",
                                 "seconds": 1.0, "tokens_in": 80, "tokens_out": 30}])
        state = llm.budget_state(self.make_args())
        self.assertTrue(state["exceeded"])
        self.assertEqual(state["on_exhausted"], "degrade")
        self.assertEqual(llm.control_degraded(self.make_args()), ("ollama", "small"))
        llm.write_status(self.make_args(), "extract", "ollama", "small", budget=state)
        with open(os.path.join(control_dir, "status", f"agent-audit-{os.getpid()}.json"),
                  encoding="utf-8") as f:
            status = json.load(f)
        self.assertEqual(status["effective"]["tier"], "medium")
        self.assertTrue(status["budget"]["exceeded"])

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
