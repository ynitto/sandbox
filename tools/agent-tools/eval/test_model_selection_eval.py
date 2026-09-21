"""Fake stage observations + fake outcomes; never contact Jev/remote APIs."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_selection_eval as ev
import model_selection_real as real

FIXTURES = json.loads((ev.HERE / "data/model-selection/fixtures.json").read_text())


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.f = copy.deepcopy(FIXTURES[0])
        self.network = patch("urllib.request.urlopen", side_effect=AssertionError("offline test used network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def rows(self, threshold=.6):
        return {r["arm"]: r for r in ev.evaluate(self.f, threshold)["rows"]}

    def test_baselines_and_oracle(self):
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows["selector-selected"]["candidate"], "fixture-small/v1")
        self.assertEqual(rows["audit-fallback"]["candidate"], "fixture-large/v1")
        self.assertEqual(rows["highest-rated-eligible"]["candidate"], "fixture-large/v1")
        self.assertEqual(rows["cheapest-eligible"]["candidate"], "fixture-small/v1")
        self.assertEqual(rows["fixture-oracle"]["candidate"], "fixture-small/v1")
        self.assertEqual(rows["audit-fallback"]["regret"]["extra_tokens_when_both_pass"], 3600)

    def test_canonical_rank_functions_are_used(self):
        with patch.object(ev.ms, "audit_order", wraps=ev.ms.audit_order) as order:
            self.rows()
        self.assertGreaterEqual(order.call_count, 3)

    def test_threshold_sweep_changes_stages_without_changing_config(self):
        before = copy.deepcopy(self.f)
        with patch.object(ev.ms.herdconfig, "select_setting", side_effect=AssertionError("read live config")):
            rows = [self.rows(t)["selector-selected"] for t in ev.THRESHOLDS]
        self.assertEqual([r["stage"] for r in rows], ["jev", "jev", "judge", "judge", "audit"])
        self.assertEqual([r["selector_tokens"] for r in rows], [101, 101, 202, 202, 202])
        self.assertEqual(self.f, before)

    def test_confidence_boundaries(self):
        self.assertEqual([ev.bucket(x) for x in (None,.59,.6,.7,.8,.9,1)],
                         ["unknown","below-0.6","0.6–0.7","0.7–0.8","0.8–0.9","0.9+","0.9+"])

    def test_stage_horizon_and_confidence_breakdowns(self):
        report = ev.report(copy.deepcopy(FIXTURES))
        self.assertEqual(sum(v["n"] for v in report["by_stage"].values()), 9)
        self.assertEqual(sum(v["n"] for v in report["by_confidence"].values()), 9)
        self.assertEqual([v["n"] for v in report["by_horizon"].values()], [3,3,3])
        self.assertEqual(set(report["threshold_sweep"]), {"0.5","0.6","0.7","0.8","0.9"})
        self.assertIsNone(report["by_arm"]["selector-selected"]["cost_by_currency"].get("USD"))

    def test_completion_is_weighted_commands_not_claimed_verdict(self):
        o = self.f["outcomes"]["fixture-small/v1"]
        o["receipt"]["commands"][1]["exit_code"] = 1
        o["receipt"]["verdict"] = "pass"
        self.f["checkpoints"][0]["weight"] = 2
        row = self.rows()["selector-selected"]
        self.assertFalse(row["verified_pass"])
        self.assertEqual(row["completion"], .75)
        self.assertEqual(row["regret"]["missed_pass"], 1)

    def test_full_completion_does_not_override_other_failed_verification(self):
        plan = ev.vc.build_plan(self.f["id"], commands=[c["command"] for c in self.f["verification_plan"]["commands"]] + ["extra acceptance"])
        self.f["verification_plan"] = plan
        for o in self.f["outcomes"].values():
            o["receipt"] = ev.vc.build_receipt(plan, result_rev=o["result_rev"],
                commands=o["receipt"]["commands"] + [{"command":"extra acceptance", "exit_code":1}])
        row = self.rows()["selector-selected"]
        self.assertEqual(row["completion"], 1)
        self.assertFalse(row["verified_pass"])

    def test_no_pass_oracle_uses_completion(self):
        self.f = copy.deepcopy(FIXTURES[7])
        row = self.rows()["fixture-oracle"]
        self.assertEqual(row["candidate"], "fixture-medium/v1")
        self.assertAlmostEqual(row["completion"], 2/3)
        self.assertFalse(row["verified_pass"])

    def test_missing_usage_is_not_zero_or_currency_conversion(self):
        self.f["outcomes"]["fixture-small/v1"].update(tokens=None, cost=1, currency=None)
        self.f["selector_observations"]["jev"]["tokens"] = None
        row = self.rows()["selector-selected"]
        self.assertIsNone(row["tokens"])
        self.assertIsNone(row["total_tokens"])
        self.assertIsNone(row["cost"])
        stats = ev.stats([row])
        self.assertEqual(stats["tokens"]["unknown_n"], 1)
        self.assertIsNone(stats["tokens"]["mean"])

    def test_no_complete_outcome_table_no_oracle(self):
        del self.f["outcomes"]["fixture-large/v1"]
        rows = self.rows()
        self.assertIsNone(rows["fixture-oracle"]["candidate"])
        self.assertIsNone(rows["selector-selected"]["regret"])

    def test_invalid_receipt_and_revision_are_not_task_failures(self):
        self.f["outcomes"]["fixture-small/v1"]["result_rev"] = "wrong"
        row = self.rows()["selector-selected"]
        self.assertEqual(row["status"], "invalid-receipt")
        self.assertIsNone(row["verified_pass"])
        self.assertEqual(ev.stats([row])["verified_n"], 0)

    def test_missing_command_is_invalid_receipt(self):
        self.f["outcomes"]["fixture-small/v1"]["receipt"]["commands"].pop()
        self.assertEqual(self.rows()["selector-selected"]["status"], "invalid-receipt")

    def test_api_error_abstention_and_missing_stage_are_distinct(self):
        self.f["selector_observations"]["jev"] = {"status":"error", "detail":"HTTP 503", "tokens":None}
        row = self.rows()["selector-selected"]
        self.assertEqual(row["stage"], "judge")
        self.assertTrue(row["verified_pass"])
        self.f["selector_observations"]["judge"]["answer"]["choice"] = "none"
        self.assertEqual(self.rows()["selector-selected"]["stage"], "audit")
        del self.f["selector_observations"]["judge"]
        row = self.rows()["selector-selected"]
        self.assertEqual(row["status"], "unobserved-stage:judge")
        self.assertIsNone(row["verified_pass"])

    def test_method_text_abstains_even_at_high_confidence(self):
        for obs in self.f["selector_observations"].values():
            obs["answer"].update(method="text", confidence=1)
        self.assertEqual(self.rows()["selector-selected"]["stage"], "audit")

    def test_context_quota_and_budget_prefilter(self):
        self.f["candidates"][0]["context_tokens"] = 1
        self.f["quotas"] = {"fixture-medium":{"blocked":True,"kind":"exhausted"}}
        self.assertTrue(all(r["candidate"] == "fixture-large/v1" for r in self.rows().values()))
        self.f = copy.deepcopy(FIXTURES[0])
        self.f["budget"] = {"exceeded":True, "on_exhausted":"degrade"}
        self.assertEqual(self.rows()["audit-fallback"]["candidate"], "fixture-small/v1")

    def test_all_dropped_production_reentry_is_not_scored(self):
        self.f["quotas"] = {c["agent_cli"]:{"blocked":True,"kind":"exhausted"} for c in self.f["candidates"]}
        run = ev.evaluate(self.f)
        self.assertIsNotNone(run["replay"]["selection"]["selected"])
        self.assertEqual(len(run["rows"]),5)
        self.assertTrue(all(r["status"] == "no-eligible-candidate" for r in run["rows"]))

    def add_resolver(self):
        policy = {"strategy":"balanced", "retry_limit":1, "no_candidate":"park", "candidates":self.f["candidates"]}
        self.f["resolver"] = {"compiled_control":{"version":2,"revision":1,
                               "workloads":{"development":{"selection_policy":policy}}}}

    def test_resolver_exclusions_and_receipt(self):
        self.add_resolver()
        self.f["resolver"]["unavailable"] = ["fixture-small/v1", "fixture-medium/v1"]
        run = ev.evaluate(self.f)
        self.assertEqual(run["rows"][0]["candidate"], "fixture-large/v1")
        self.assertEqual(run["replay"]["decision"]["selection_source"], "qualified-candidate")
        self.f["resolver"]["budget_state"] = {"hard_exhausted":True}
        self.assertEqual(self.rows()["selector-selected"]["status"], "no-eligible-candidate")

    def test_resolver_cannot_hide_unobserved_stage_as_audit(self):
        self.add_resolver()
        self.f["selector_observations"] = {}
        self.assertEqual(self.rows()["selector-selected"]["status"], "unobserved-stage:jev")

    def test_abstain_is_not_an_incorrect_choice(self):
        with patch.object(ev.ms, "select", return_value={"selected":None, "attempts":[]}):
            row = self.rows()["selector-selected"]
        self.assertEqual(row["status"], "abstain")
        self.assertIsNone(row["verified_pass"])

    def test_purpose_ratings_snapshot_is_used(self):
        for c in self.f["candidates"]:
            del c["rating"]
        self.f["ratings"] = {"rows":[{"model":"fixture-small", "purpose":"worker", "pass_rate":.99,
                                     "average_tokens":200,"outcome_runs":4}]}
        self.assertEqual(self.rows()["audit-fallback"]["candidate"], "fixture-small/v1")

    def test_stage_metrics_use_outcomes_not_choice_accuracy(self):
        self.f["outcomes"]["fixture-small/v1"]["receipt"]["commands"][0]["exit_code"] = 1
        result = ev.report([self.f])
        group = result["by_stage"]["jev"]
        self.assertEqual(group["verified_pass_rate"], 0)
        self.assertAlmostEqual(group["completion"]["mean"], 2/3)
        self.assertEqual(group["tokens"]["mean"], 800)
        self.assertEqual(group["wall_seconds"]["mean"], 30)
        self.assertEqual(result["threshold_sweep"]["0.9"]["audit_fallback_rate"], 1)

    def test_synthetic_and_measured_results_cannot_be_mixed(self):
        measured = copy.deepcopy(self.f)
        measured["provenance"] = "measured"
        with self.assertRaises(ValueError):
            ev.report([self.f, measured])

    def test_inconclusive_and_cli_error_excluded_from_verified_denominator(self):
        o = self.f["outcomes"]["fixture-small/v1"]
        o["receipt"]["commands"][0].update(exit_code=None, inconclusive=True)
        self.assertIsNone(self.rows()["selector-selected"]["verified_pass"])
        self.assertIsNone(self.rows()["selector-selected"]["completion"])
        o["status"] = "cli-error"
        self.assertEqual(self.rows()["selector-selected"]["status"], "cli-error")

    def test_reject_bad_fixture(self):
        for change in (lambda f:f.update(objective="universal-utility"),
                       lambda f:f["checkpoints"][0].update(weight=-1),
                       lambda f:f["checkpoints"][0].update(command_index=99)):
            f=copy.deepcopy(self.f);change(f)
            with self.assertRaises(ValueError):ev.validate(f)


class RealAdapterTests(unittest.TestCase):
    def test_raw_usage_distinguishes_explicit_zero_from_missing(self):
        f = copy.deepcopy(FIXTURES[0])
        response = {"answers":{"candidate":{"choice":"fixture-small/v1", "confidence":.95}}}
        with patch.object(ev.ms, "jev_setting", return_value={"enabled":True, "endpoint":"fake", "api_key":"fake"}), \
                patch.object(ev.ms, "_spec_of", return_value=None), \
                patch.object(ev.ms, "post_jev", return_value=response), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            observations, _ = real.capture_selector(f)
            self.assertIsNone(observations["jev"]["tokens"])
            response["usage"] = {"input_tokens":0, "output_tokens":0}
            observations, _ = real.capture_selector(f)
            self.assertEqual(observations["jev"]["tokens"], 0)
            response["usage"] = {"input_tokens":20, "output_tokens":2}
            observations, _ = real.capture_selector(f)
            self.assertEqual(observations["jev"]["tokens"], 22)

    def test_opt_in_adapter_runs_fake_cli_in_independent_archives_and_imports_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo=root/"source";repo.mkdir();out=root/"runs";out.mkdir()
            (repo/"result.txt").write_text("bad")
            real.git("init","-q",cwd=repo)
            rev=real.snapshot(repo,"base")
            f=copy.deepcopy(FIXTURES[0])
            f["verification_plan"]=ev.vc.build_plan(f["id"],commands=["python3 -c \"from pathlib import Path; assert Path('result.txt').read_text()=='good'\""],workspace="sandbox")
            f["checkpoints"]=[{"command_index":0,"weight":1}]
            f["real_task"]={"base_revision":rev,"verification_revision":rev,"test_files":[]}
            candidates=[{"agent_cli":"fake-a","model":"v1"},{"agent_cli":"fake-b","model":"v1"}]
            def command(name, model, prompt, **kwargs):
                return {"argv":[sys.executable,"-c", "from pathlib import Path; import sys; assert Path('result.txt').read_text()=='bad'; Path('result.txt').write_text('good'); print('@agent-usage tokens_in=8 tokens_out=2',file=sys.stderr)"],"stdin":None,"env":{}}
            with patch.object(engine := ev.engine,"REPO",repo), \
                    patch.object(real,"capture_selector",return_value=({"jev":{"status":"not-configured"},"judge":{"status":"not-available"}},{})), \
                    patch.object(engine,"headless_cmd",side_effect=command), \
                    patch.object(engine,"load_env",return_value={}), \
                    patch.object(ev.ms,"_spec_of",return_value=None), \
                    patch.object(ev.ms,"quota_observations",return_value={}), \
                    patch.object(ev.ms,"budget_summary",return_value=None):
                fixtures=real.collect([f],candidates,out,30)
            for cid,o in fixtures[0]["outcomes"].items():
                self.assertEqual(o["tokens"],10)
                self.assertTrue(ev.outcome(fixtures[0],cid)["verified_pass"])
            self.assertEqual((repo/"result.txt").read_text(),"bad")
            self.assertTrue((out/"measured-fixtures.json").exists())


if __name__ == "__main__":
    unittest.main()
