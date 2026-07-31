"""agent-loop のノード予算 v2（トークン一次・rates 推定）と agent-control（lifecycle・
status）の単体テスト。tmux / エージェント CLI 不要・標準ライブラリのみ。

kiro-loop の同名テストのクローン（agent-loop は kiro-loop の後継クローン）。

    python3 -m pytest tools/agent-loop/test/ -q
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class NodeBudgetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="al-nb-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)

    def _config(self, cfg):
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _ledger(self, records):
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        name = time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"
        with open(os.path.join(led, name), "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_no_config_means_unlimited(self):
        self.assertIsNone(al._node_budget_state())

    def test_all_zero_limits_mean_unlimited(self):
        self._config({"execution_minutes": 0, "tokens": 0})
        self.assertIsNone(al._node_budget_state())

    def test_token_limit_counts_all_workloads(self):
        self._config({"tokens": 1000})
        self._ledger([
            {"workload": "routine", "tokens_out": 400},
            {"workload": "flow", "tokens_out": 400},
        ])
        st = al._node_budget_state()
        self.assertFalse(st["exceeded"])
        self._ledger([{"workload": "project", "tokens_out": 300}])
        self.assertTrue(al._node_budget_state()["exceeded"])

    def test_unmeasured_rows_are_estimated_by_rate(self):
        """トークン未報告の行は 秒 × レート で推定する（agent-loop 自身の記帳がこれ）。"""
        self._config({"tokens": 1000, "rates": {"default_tokens_per_second": 10}})
        self._ledger([{"workload": "routine", "seconds": 50}])   # 500 tok 相当
        self.assertFalse(al._node_budget_state()["exceeded"])
        self._ledger([{"workload": "routine", "seconds": 60}])   # 累計 1100 tok
        self.assertTrue(al._node_budget_state()["exceeded"])

    def test_rate_resolution_prefers_cli_model(self):
        cfg = {"rates": {"per_cli": {"kiro:auto": 20, "kiro": 5},
                         "default_tokens_per_second": 1}}
        self.assertEqual(al._node_budget_rate(cfg, "kiro", "auto"), 20)
        self.assertEqual(al._node_budget_rate(cfg, "kiro", ""), 5)
        self.assertEqual(al._node_budget_rate(cfg, "claude", ""), 1)

    def test_measured_tokens_win_over_estimate(self):
        cfg = {"rates": {"default_tokens_per_second": 100}}
        self.assertEqual(al._row_tokens({"tokens_in": 3, "tokens_out": 4, "seconds": 99}, cfg), 7)

    def test_soft_threshold_before_exceeding(self):
        self._config({"tokens": 1000, "allocation": {"soft_ratio": 0.9}})
        self._ledger([{"workload": "routine", "tokens_out": 950}])
        st = al._node_budget_state()
        self.assertTrue(st["soft"])
        self.assertFalse(st["exceeded"])

    def test_time_limit_still_applies(self):
        self._config({"execution_minutes": 1})
        self._ledger([{"workload": "routine", "seconds": 61}])
        self.assertTrue(al._node_budget_state()["exceeded"])

    def test_on_exhausted_defaults_to_pause(self):
        self._config({"tokens": 10})
        self._ledger([{"workload": "routine", "tokens_out": 99}])
        self.assertEqual(al._node_budget_state()["on_exhausted"], "pause")

    def test_record_appends_workload_and_tool(self):
        al._node_budget_record(1.5, ref="%3", agent_cli="kiro", model="auto")
        led = os.path.join(self.dir, "ledger",
                           time.strftime("%Y%m%d", time.gmtime()) + ".jsonl")
        with open(led, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["workload"], "routine")
        self.assertEqual(rec["tool"], "agent-loop")
        self.assertEqual(rec["seconds"], 1.5)
        self.assertEqual(rec["agent_cli"], "kiro")

    def test_record_ignores_empty_measurements(self):
        al._node_budget_record(0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "ledger")))

    def test_broken_ledger_lines_are_skipped(self):
        self._config({"tokens": 100})
        led = os.path.join(self.dir, "ledger")
        os.makedirs(led, exist_ok=True)
        name = time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"
        with open(os.path.join(led, name), "w", encoding="utf-8") as f:
            f.write("これは JSON ではない\n")
            f.write(json.dumps({"workload": "routine", "tokens_out": 10}) + "\n")
        self.assertFalse(al._node_budget_state()["exceeded"])


class ControlLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="al-ctl-lc-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.environ["AGENT_CONTROL_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        al._CONTROL_CACHE["mtime"] = None

    def _control(self, obj):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)
        al._CONTROL_CACHE["mtime"] = None

    def test_defaults_to_run_without_control(self):
        self.assertEqual(al._control_lifecycle(), "run")

    def test_reads_routine_lifecycle(self):
        self._control({"workloads": {"routine": {"lifecycle": "pause"}}})
        self.assertEqual(al._control_lifecycle(), "pause")
        self._control({"workloads": {"routine": {"lifecycle": "stop"}}})
        self.assertEqual(al._control_lifecycle(), "stop")

    def test_other_workloads_do_not_leak(self):
        """flow を止めても定常業務（routine）は動き続ける。"""
        self._control({"workloads": {"flow": {"lifecycle": "stop"}}})
        self.assertEqual(al._control_lifecycle(), "run")

    def test_broken_control_falls_back_to_run(self):
        with open(os.path.join(self.dir, "control.json"), "w", encoding="utf-8") as f:
            f.write("{ 壊れた JSON")
        al._CONTROL_CACHE["mtime"] = None
        self.assertEqual(al._control_lifecycle(), "run")

    def test_status_carries_budget_flags(self):
        al._write_status(lifecycle="pause", budget={"exceeded": True, "soft": False})
        status_dir = os.path.join(self.dir, "status")
        files = [n for n in os.listdir(status_dir) if n.endswith(".json")]
        with open(os.path.join(status_dir, files[0]), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["lifecycle"], "pause")
        self.assertEqual(rec["budget"], {"exceeded": True, "soft": False})

    def test_stopped_reason_is_recorded(self):
        al._write_stopped_reason("node-budget")
        path = al._STATE_DIR / f"stopped-{os.getpid()}.json"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["stopped_reason"], "node-budget")
        self.assertEqual(rec["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
