"""agentcore.modelselect と `agent-herd select` の契約。

背骨は 4 つ:

1. **順は jev → judge → audit で、どの段が決めたかを隠さない。** 上の段が使えない・
   決めない（確度不足 / どれでもない）ときだけ次へ倒れ、`attempts` に記録が残る。
2. **決定的に落とせる候補は LLM に訊く前に落とす。** quota 枯渇・レート制限・文脈不足・
   縮退指定。残りが 1 件なら LLM を呼ばない。
3. **最後の段は必ず決める（LLM 不使用）。** agent-audit の格付け → policy の rank →
   相対コストの順。
4. **Resolver に差し込んでも policy の外へ出ない。** selector が候補列に無い候補を返しても無視。
"""
from __future__ import annotations

import io
import json
import math
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import herdcli, herdconfig, judge, modelselect  # noqa: E402

CLAUDE = {"agent_cli": "claude", "model": "sonnet"}
OLLAMA = {"agent_cli": "ollama", "model": "gemma4:e4b"}
CLOUD2 = {"agent_cli": "codex", "model": "gpt-6"}


def _ollama_response(top: "dict[str, float]") -> dict:
    """ollama `/api/chat` の応答（logprobs つき）——test_judge と同じ形。"""
    first = next(iter(top))
    return {"message": {"role": "assistant", "content": first},
            "logprobs": [{"token": first, "logprob": math.log(top[first]),
                          "top_logprobs": [{"token": t, "logprob": math.log(p)}
                                           for t, p in top.items()]}],
            "prompt_eval_count": 50, "eval_count": 1}


def _judge_prefers(cli):
    def respond(body):
        prompt = body["messages"][0]["content"]
        material = json.loads(prompt.split("<<<\n", 1)[1].split("\n>>>", 1)[0])
        candidate = material.get("candidate")
        yes = 0.95 if candidate is None or candidate["agent_cli"] == cli else 0.05
        return _ollama_response({"A": yes, "B": 1 - yes})
    return respond


def _jev_response(choice: str, probs: dict, confidence=None) -> dict:
    return {"model": "jev-1.13.0",
            "answers": {"candidate": {"type": "choice", "choice": choice,
                                      "confidence": probs[choice] if confidence is None else confidence,
                                      "probabilities": probs}},
            "usage": {"input_tokens": 380, "output_tokens": 45}}


class IsolatedHome(unittest.TestCase):
    """設定ファイル・台帳を一時ディレクトリへ隔離し、環境の Jev キーも消す。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-herd-select-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        env = {"AGENT_PROJECT_AGENTS_HOME": self._tmp.name,
               "AGENT_BUDGET_DIR": str(self.home / "budget")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(modelselect.JEV_API_KEY_ENV, None)


class CandidateShapeTests(IsolatedHome):
    def test_string_and_dict_candidates_normalize_and_dedupe(self):
        out = modelselect.normalize_candidates(["Claude/sonnet", CLAUDE, "ollama", OLLAMA])
        self.assertEqual([modelselect.candidate_id(c) for c in out],
                         ["claude/sonnet", "ollama", "ollama/gemma4:e4b"])

    def test_empty_or_broken_candidates_are_refused(self):
        with self.assertRaises(modelselect.SelectError):
            modelselect.normalize_candidates([])
        with self.assertRaises(modelselect.SelectError):
            modelselect.normalize_candidates([{"model": "x"}])

    def test_describe_reads_cost_and_site_from_bundled_definitions(self):
        cloud = modelselect.describe_candidate(CLAUDE)
        local = modelselect.describe_candidate(OLLAMA)
        self.assertEqual((cloud["site"], cloud["relative_cost"]), ("cloud", 1.0))
        self.assertEqual((local["site"], local["relative_cost"]), ("local", 0.0))
        self.assertEqual(local["id"], "ollama/gemma4:e4b")

    def test_ratings_rows_attach_by_purpose_then_model(self):
        ratings = {"rows": [
            {"purpose": "review", "model": "sonnet", "pass_rate": 0.9, "average_tokens": 1200,
             "outcome_runs": 10, "rank": 1},
            {"purpose": "worker", "model": "sonnet", "pass_rate": 0.7, "average_tokens": 900,
             "outcome_runs": 4, "rank": 2}]}
        cand = modelselect.describe_candidate(CLAUDE, ratings=ratings, purpose="worker")
        self.assertEqual(cand["rating"]["pass_rate"], 0.7)
        cand = modelselect.describe_candidate(CLAUDE, ratings=ratings, purpose="planner")
        self.assertNotIn("rating", cand, "別用途の実績を流用しない")
        self.assertNotIn("rating", modelselect.describe_candidate(OLLAMA, ratings=ratings))


class QuotaAndPrefilterTests(IsolatedHome):
    def _ledger(self, rows):
        led = self.home / "budget" / "ledger"
        led.mkdir(parents=True)
        (led / "20260920.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_quota_observations_take_latest_and_expire_rate_limits(self):
        self._ledger([
            {"ts": "2026-09-20T01:00:00Z", "agent_cli": "claude", "event": "quota",
             "quota_kind": "exhausted"},
            {"ts": "2026-09-20T02:00:00Z", "agent_cli": "codex", "event": "quota",
             "quota_kind": "rate_limit", "reset_at": "2026-09-20T02:30:00Z"},
            {"ts": "2026-09-20T02:00:00Z", "agent_cli": "kiro", "event": "quota_snapshot",
             "quota_used_percent": 85},
            {"ts": "2026-09-20T00:00:00Z", "agent_cli": "claude", "event": "quota",
             "quota_kind": "rate_limit"},
        ])
        import datetime as dt
        now = dt.datetime(2026, 9, 20, 2, 0, tzinfo=dt.timezone.utc).timestamp()
        q = modelselect.quota_observations(now=now)
        self.assertTrue(q["claude"]["blocked"])
        self.assertEqual(q["claude"]["kind"], "exhausted", "最新の行が勝つ")
        self.assertTrue(q["codex"]["blocked"])
        self.assertEqual(q["kiro"], {"kind": None, "reset_at": None, "used_percent": 85,
                                     "observed_at": "2026-09-20T02:00:00Z", "blocked": False})
        later = modelselect.quota_observations(now=now + 3600)
        self.assertFalse(later["codex"]["blocked"], "reset_at を過ぎれば解ける")

    def test_prefilter_drops_blocked_small_context_and_cloud_under_degrade(self):
        described = [
            dict(modelselect.describe_candidate(CLAUDE), quota={"blocked": True, "kind": "exhausted"}),
            dict(modelselect.describe_candidate(OLLAMA), context_tokens=100),
            modelselect.describe_candidate(CLOUD2),
            dict(modelselect.describe_candidate({"agent_cli": "aider", "model": "gemma4:12b"})),
        ]
        profile = modelselect.prompt_profile("x" * 2000)          # ≈ 500 tokens
        kept, dropped = modelselect.prefilter(described, profile)
        self.assertEqual([c["id"] for c in kept], ["codex/gpt-6", "aider/gemma4:12b"])
        self.assertEqual([d["reason"] for d in dropped], ["quota-exhausted", "context-too-small"])
        kept, dropped = modelselect.prefilter(
            described, profile, budget={"exceeded": True, "on_exhausted": "degrade"})
        self.assertEqual([c["id"] for c in kept], ["aider/gemma4:12b"])
        self.assertIn("budget-degrade", [d["reason"] for d in dropped])

    def test_prefilter_never_empties_the_list(self):
        described = [dict(modelselect.describe_candidate(CLAUDE),
                          quota={"blocked": True, "kind": "rate_limit"})]
        kept, dropped = modelselect.prefilter(described, modelselect.prompt_profile("hi"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["reason"], "quota-rate_limit")


class StageOrderTests(IsolatedHome):
    JEV = {"enabled": True, "api_key": "k", "endpoint": "https://example.invalid/v1",
           "model": "jev-latest"}

    def test_single_candidate_after_prefilter_skips_every_llm(self):
        called = []
        result = modelselect.select("do it", [CLAUDE, OLLAMA],
                                    quotas={"claude": {"blocked": True, "kind": "exhausted"}},
                                    jev_setting_override=self.JEV,
                                    jev_request=lambda body: called.append(body),
                                    judge_request=lambda body: called.append(body))
        self.assertEqual(result["selected"], OLLAMA)
        self.assertEqual(result["stage"], "audit")
        self.assertEqual(called, [])
        self.assertEqual(result["dropped"][0]["id"], "claude/sonnet")

    def test_jev_decides_first_and_its_payload_has_the_jev_shape(self):
        seen = []

        def jev(body):
            seen.append(body)
            return _jev_response("claude/sonnet", {"claude/sonnet": 0.8, "ollama/gemma4:e4b": 0.15,
                                                   "none": 0.05})
        result = modelselect.select("refactor the auth module carefully", [CLAUDE, OLLAMA],
                                    purpose="worker", quotas={}, jev_setting_override=self.JEV,
                                    jev_request=jev, judge_request=lambda b: self.fail("judge called"))
        self.assertEqual(result["selected"], CLAUDE)
        self.assertEqual(result["stage"], "jev")
        self.assertEqual(result["confidence"], 0.8)
        self.assertEqual(result["usage"], {"tokens_in": 380, "tokens_out": 45})
        body = seen[0]
        self.assertEqual(body["model"], "jev-latest")
        question = body["questions"]["candidate"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), {"claude/sonnet", "ollama/gemma4:e4b", "none"})
        self.assertEqual(body["state"]["task"]["purpose"], "worker")
        self.assertEqual([c["id"] for c in body["state"]["candidates"]],
                         ["claude/sonnet", "ollama/gemma4:e4b"])
        self.assertEqual(result["attempts"][0]["outcome"], "chosen")

    def test_jev_low_confidence_or_none_falls_to_judge(self):
        for jev in (lambda b: _jev_response("claude/sonnet", {"claude/sonnet": 0.5, "ollama/gemma4:e4b": 0.5}),
                    lambda b: _jev_response("none", {"claude/sonnet": 0.1, "ollama/gemma4:e4b": 0.1,
                                                     "none": 0.8})):
            result = modelselect.select("summarize", [CLAUDE, OLLAMA], quotas={},
                                        jev_setting_override=self.JEV, jev_request=jev,
                                        judge_model="gemma4:e4b",
                                        judge_request=_judge_prefers("ollama"))
            self.assertEqual(result["stage"], "judge")
            self.assertEqual(result["selected"], OLLAMA)
            self.assertIn(result["attempts"][0]["outcome"], ("low-confidence", "none-of-them"))

    def test_jev_error_is_recorded_and_the_chain_continues(self):
        def jev(body):
            raise modelselect.SelectError("Jev に接続できません: boom")
        result = modelselect.select("summarize", [CLAUDE, OLLAMA], quotas={},
                                    jev_setting_override=self.JEV, jev_request=jev,
                                    judge_model="gemma4:e4b",
                                    judge_request=_judge_prefers("claude"))
        self.assertEqual(result["stage"], "judge")
        self.assertEqual(result["selected"], CLAUDE)
        self.assertEqual(result["attempts"][0], {"stage": "jev", "outcome": "error",
                                                 "detail": "Jev に接続できません: boom"})

    def test_judge_text_readout_has_no_confidence_and_falls_to_audit(self):
        no_logprobs = {"message": {"role": "assistant", "content": "A"},
                       "prompt_eval_count": 10, "eval_count": 1}
        result = modelselect.select("summarize", [CLAUDE, OLLAMA], quotas={},
                                    jev_setting_override={"enabled": False},
                                    judge_model="gemma4:e4b", judge_request=lambda b: no_logprobs)
        self.assertEqual(result["stage"], "audit")
        self.assertEqual(result["attempts"][1]["outcome"], "no-confidence")

    def test_judge_gate_follows_judge_model_setting(self):
        fit_cloud = [modelselect.describe_candidate(CLAUDE), modelselect.describe_candidate(CLOUD2)]
        fit_mixed = [modelselect.describe_candidate(CLAUDE), modelselect.describe_candidate(OLLAMA)]
        self.assertIsNone(modelselect.judge_model_for(fit_cloud), "auto: クラウドだけなら叩かない")
        self.assertEqual(modelselect.judge_model_for(fit_mixed), "gemma4:e4b")
        herdconfig.set_value("judge.model", "gemma4:12b")
        self.assertEqual(modelselect.judge_model_for(fit_cloud), "gemma4:12b")
        herdconfig.set_value("judge.model", "off")
        self.assertIsNone(modelselect.judge_model_for(fit_mixed))

    def test_audit_stage_ranks_by_rating_then_rank_then_cost(self):
        ratings = {"rows": [
            {"purpose": "worker", "model": "gpt-6", "pass_rate": 0.9, "average_tokens": 3000,
             "outcome_runs": 8, "rank": 1},
            {"purpose": "worker", "model": "sonnet", "pass_rate": 0.9, "average_tokens": 1500,
             "outcome_runs": 8, "rank": 2}]}
        result = modelselect.select("x", [OLLAMA, CLOUD2, CLAUDE], purpose="worker", quotas={},
                                    ratings=ratings, stages=("audit",))
        self.assertEqual(result["selected"], CLAUDE, "同じ PASS 率なら平均消費が少ないほう")
        self.assertEqual(result["stage"], "audit")
        self.assertIn("格付け", result["reason"])
        order = result["attempts"][0]["order"]
        self.assertEqual(order[:2], ["claude/sonnet", "codex/gpt-6"])
        self.assertEqual(order[2], "ollama/gemma4:e4b", "格付けの無い候補は後ろ")

        by_rank = modelselect.select("x", [dict(CLAUDE, rank=2), dict(OLLAMA, rank=1)],
                                     quotas={}, stages=("audit",))
        self.assertEqual(by_rank["selected"], OLLAMA)
        self.assertIn("順位", by_rank["reason"])
        by_cost = modelselect.select("x", [CLAUDE, OLLAMA], quotas={}, stages=("audit",))
        self.assertEqual(by_cost["selected"], OLLAMA, "何も無ければ安いほう")

    def test_min_confidence_comes_from_the_config_file(self):
        herdconfig.set_value("select.min_confidence", "0.95")
        result = modelselect.select("x", [CLAUDE, OLLAMA], quotas={},
                                    jev_setting_override={"enabled": False},
                                    judge_model="gemma4:e4b",
                                    judge_request=lambda b: _ollama_response({"A": 0.9, "B": 0.1}))
        self.assertIsNone(result["selected"])
        self.assertEqual(result["attempts"][1]["outcome"], "no-adequate-candidate")

    def test_empty_prompt_is_refused(self):
        with self.assertRaises(modelselect.SelectError):
            modelselect.select("  ", [CLAUDE, OLLAMA], quotas={})


class JevAdapterTests(IsolatedHome):
    def test_setting_reads_config_then_env(self):
        self.assertFalse(modelselect.jev_setting()["enabled"])
        with mock.patch.dict(os.environ, {modelselect.JEV_API_KEY_ENV: "env-key"}):
            setting = modelselect.jev_setting()
        self.assertEqual((setting["api_key"], setting["source"]), ("env-key", "env"))
        self.assertEqual(setting["endpoint"], modelselect.JEV_DEFAULT_ENDPOINT)
        herdconfig.set_value("select.jev.api_key", "file-key")
        herdconfig.set_value("select.jev.endpoint", "https://gateway.example/v1/systemone")
        with mock.patch.dict(os.environ, {modelselect.JEV_API_KEY_ENV: "env-key"}):
            setting = modelselect.jev_setting()
        self.assertEqual((setting["api_key"], setting["source"]), ("file-key", "config"))
        self.assertEqual(setting["endpoint"], "https://gateway.example/v1/systemone")
        herdconfig.set_value("select.jev.api_key", "off")
        self.assertFalse(modelselect.jev_setting()["enabled"], "off は環境変数があっても使わない")

    def test_answer_outside_the_options_is_an_error_not_a_pick(self):
        question = modelselect.build_question([modelselect.describe_candidate(CLAUDE),
                                               modelselect.describe_candidate(OLLAMA)])
        with self.assertRaises(modelselect.SelectError):
            modelselect.read_jev_answer(_jev_response("kiro/opus", {"kiro/opus": 1.0}), question)
        with self.assertRaises(modelselect.SelectError):
            modelselect.read_jev_answer({"answers": {}}, question)

    def test_config_json_masks_the_api_key(self):
        herdconfig.set_value("select.jev.api_key", "secret")
        info = herdconfig.describe()
        self.assertEqual(info["select"]["jev"]["api_key"], "(set)")
        self.assertNotIn("secret", json.dumps(info))
        self.assertEqual(herdconfig.select_setting()["jev"]["api_key"], "secret")
        herdconfig.unset_value("select.jev.api_key")
        self.assertEqual(herdconfig.select_setting()["jev"], {})
        with self.assertRaises(herdconfig.ConfigError):
            herdconfig.set_value("select.min_confidence", "1.5")


class ResolverSelectorTests(IsolatedHome):
    def test_selector_memoizes_per_candidate_set(self):
        calls = []

        def jev(body):
            calls.append(body)
            return _jev_response("ollama/gemma4:e4b", {"claude/sonnet": 0.1, "ollama/gemma4:e4b": 0.9})
        selector = modelselect.resolver_selector(
            "x", quotas={}, jev_setting_override=StageOrderTests.JEV, jev_request=jev)
        candidates = [dict(CLAUDE, rank=1), dict(OLLAMA, rank=2)]
        first = selector(candidates)
        second = selector(candidates)
        self.assertEqual(first["agent_cli"], "ollama")
        self.assertEqual(first["stage"], "jev")
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_resolver_uses_the_selector_only_inside_the_policy(self):
        from agentcore.executionresolver import receipt_execution_decision, resolve_execution
        policy = {"strategy": "balanced", "retry_limit": 1, "no_candidate": "park",
                  "candidates": [dict(CLAUDE, rank=1), dict(OLLAMA, rank=2)]}
        control = {"version": 2, "revision": 1, "workloads": {"flow": {"selection_policy": policy}}}
        seen = []

        def picks_rank2(cands):
            seen.append([c["agent_cli"] for c in cands])
            return {"agent_cli": "ollama", "model": "gemma4:e4b", "stage": "judge",
                    "confidence": 0.9, "reason": "cheap enough"}
        decision = resolve_execution("flow", compiled_control=control, selector=picks_rank2)
        self.assertEqual(decision["selected"], OLLAMA)
        self.assertEqual(decision["selection_source"], "qualified-candidate")
        self.assertEqual(decision["fallback_candidates"], [CLAUDE])
        self.assertEqual(decision["selector"]["stage"], "judge")
        self.assertEqual(receipt_execution_decision(decision)["selector"]["confidence"], 0.9)
        self.assertEqual(seen, [["claude", "ollama"]])

        outside = resolve_execution("flow", compiled_control=control,
                                    selector=lambda c: {"agent_cli": "kiro", "model": "opus"})
        self.assertEqual(outside["selected"], CLAUDE, "policy の外の候補は無視して rank 順")
        self.assertNotIn("selector", outside)

        broken = resolve_execution("flow", compiled_control=control,
                                   selector=lambda c: 1 / 0)
        self.assertEqual(broken["selected"], CLAUDE, "判断の失敗で実行を止めない")

        single = resolve_execution("flow", compiled_control=control,
                                   unavailable={"claude/sonnet"},
                                   selector=lambda c: self.fail("1 件なら呼ばない"))
        self.assertEqual(single["selected"], OLLAMA)


class SelectCommandTests(IsolatedHome):
    def _run(self, argv, stdin="write tests for the parser", **kw):
        out, err = io.StringIO(), io.StringIO()
        rc = herdcli.cmd_select(argv, out=out, err=err, stdin=io.StringIO(stdin), **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_selects_and_reports_stage(self):
        rc, out, err = self._run(["--candidate", "claude/sonnet", "--candidate", "ollama/gemma4:e4b",
                                  "--stages", "audit"])
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["selected"], OLLAMA)
        self.assertEqual(data["stage"], "audit")
        self.assertIn("@agent-usage tokens_in=0 tokens_out=0", err)

    def test_json_carries_state_and_attempts(self):
        rc, out, _ = self._run(["--candidate", "claude", "--candidate", "ollama", "--json",
                                "--purpose", "review"],
                               judge_request=_judge_prefers("claude"))
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["stage"], "judge")
        self.assertEqual(data["selected"]["agent_cli"], "claude")
        self.assertEqual(data["state"]["task"]["purpose"], "review")
        self.assertEqual([a["stage"] for a in data["attempts"]], ["jev", "judge"])
        self.assertEqual(data["attempts"][0]["outcome"], "not-configured")

    def test_argument_errors_are_exit_2(self):
        self.assertEqual(self._run(["--candidate"])[0], 2)
        self.assertEqual(self._run(["--stages", "jev,nope"])[0], 2)
        self.assertEqual(self._run(["--min-confidence", "2"])[0], 2)
        self.assertEqual(self._run(["--bogus"])[0], 2)
        self.assertEqual(self._run(["--candidate", "claude"], stdin="")[0], 2)
        self.assertEqual(self._run(["--ratings", str(self.home / "missing.json")])[0], 2)

    def test_ratings_file_feeds_the_audit_stage(self):
        path = self.home / "ratings.json"
        path.write_text(json.dumps({"rows": [
            {"purpose": "worker", "model": "sonnet", "pass_rate": 0.95, "average_tokens": 800,
             "outcome_runs": 6, "rank": 1}]}), encoding="utf-8")
        rc, out, _ = self._run(["--candidate", "claude/sonnet", "--candidate", "ollama/gemma4:e4b",
                                "--ratings", str(path), "--purpose", "worker", "--stages", "audit"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["selected"], CLAUDE)

    def test_main_dispatches_select_and_help(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = herdcli.main(["select", "--help"], prog="agent-herd")
        self.assertEqual(rc, 0)
        self.assertIn("jev", out.getvalue())
        self.assertIn("select", herdcli.HELP)
        self.assertIn("select.jev.api_key", herdcli.CONFIG_HELP)


class IndependentFitTests(IsolatedHome):
    def test_candidate_order_does_not_change_inputs_or_tie_break(self):
        records = []
        def capture(body):
            material = json.loads(body["messages"][0]["content"].split("<<<\n", 1)[1].split("\n>>>", 1)[0])
            records.append(material)
            return _ollama_response({"A": 0.95, "B": 0.05})
        results = []
        for candidates in ([CLAUDE, OLLAMA], [OLLAMA, CLAUDE]):
            results.append(modelselect.select("A simple request", candidates, quotas={},
                stages=("judge", "audit"), judge_model="gemma4:e4b", judge_request=capture))
        self.assertEqual(results[0]["selected"], results[1]["selected"])
        self.assertEqual(results[0]["selected"], OLLAMA)
        self.assertEqual(records[0], records[3])
        self.assertEqual(records[1], records[5])
        self.assertEqual(records[2], records[4])
        for item in (records[1], records[2]):
            self.assertNotIn("relative_cost", item["candidate"])
            self.assertNotIn("site", item["candidate"])
            self.assertNotIn("candidates", item)
            self.assertIn("model_identity", item["candidate"])
        self.assertEqual(results[0]["usage"], {"tokens_in": 150, "tokens_out": 3})
        self.assertEqual(len(results[0]["attempts"][0]["candidate_fits"]), 2)

    def test_completed_negative_fits_do_not_fall_back_to_cheapest(self):
        result = modelselect.select("Unsuited work", [CLAUDE, OLLAMA], quotas={},
            stages=("judge", "audit"), judge_model="gemma4:e4b",
            judge_request=lambda b: _ollama_response({"A": 0.05, "B": 0.95}))
        self.assertIsNone(result["selected"])
        self.assertEqual(result["attempts"][0]["outcome"], "no-adequate-candidate")
        self.assertFalse(any(a["stage"] == "audit" for a in result["attempts"]))

    def test_unknown_candidate_cannot_hide_negative_fits_in_audit_fallback(self):
        def respond(body):
            content = body["messages"][0]["content"]
            material = json.loads(content.split("<<<\n", 1)[1].split("\n>>>", 1)[0])
            candidate = material.get("candidate")
            if candidate and candidate["agent_cli"] == "ollama":
                return {"message": {"content": "A"}, "prompt_eval_count": 10, "eval_count": 1}
            return _ollama_response({"A": .05, "B": .95})
        result = modelselect.select("Unknown capability", [CLAUDE, OLLAMA], quotas={},
            stages=("judge", "audit"), judge_model="gemma4:e4b", judge_request=respond)
        self.assertIsNone(result["selected"])
        self.assertEqual(result["attempts"][0]["outcome"], "no-adequate-candidate")

    def test_evidence_is_scoped_measured_and_identity_matched(self):
        rows = [
            {"agent_cli": "other", "model": "sonnet", "purpose": "worker", "outcome_runs": 9, "pass_rate": 1},
            {"model": "sonnet", "purpose": "worker", "outcome_runs": 5, "pass_rate": .8},
            {"agent_cli": "claude", "model": "sonnet", "purpose": "worker", "outcome_runs": 3, "pass_rate": .5,
             "constraints": {"bounded_input": True}, "source": "fixture"},
        ]
        for data in (rows, list(reversed(rows))):
            rating = modelselect.describe_candidate(CLAUDE, purpose="worker", ratings=data)["rating"]
            self.assertEqual(rating["runs"], 3)
            self.assertEqual(rating["source"], "fixture")
            self.assertEqual(rating["constraints"], {"bounded_input": True})
            self.assertEqual(rating["identity_match"], "cli-and-model")
        for count in (0, None, -1, True):
            self.assertNotIn("rating", modelselect.describe_candidate(
                {**CLAUDE, "rating": {"runs": count, "pass_rate": 1}}))
        self.assertNotIn("rating", modelselect.describe_candidate(
            CLAUDE, purpose="planner", ratings=rows))
        self.assertIn("not verified", modelselect.describe_candidate(
            CLAUDE, purpose="worker", ratings=rows)["rating"]["metric"])

    def test_model_source_is_explicit_default_or_unresolved(self):
        self.assertEqual(modelselect.describe_candidate(CLAUDE)["model_source"], "explicit")
        self.assertEqual(modelselect.describe_candidate({"agent_cli": "ollama"})["model_source"], "definition-default")
        with mock.patch.object(modelselect, "_spec_of", return_value={"name": "custom", "relative_cost": 1}):
            item = modelselect.describe_candidate({"agent_cli": "custom"})
        self.assertEqual(item["model"], "")
        self.assertEqual(item["model_source"], "provider-default-not-resolved")


class JudgeOtherMappingTests(IsolatedHome):
    def test_judge_other_maps_to_none(self):
        fit = [modelselect.describe_candidate(CLAUDE), modelselect.describe_candidate(OLLAMA)]
        question = modelselect.build_question(fit)
        answers = modelselect.ask_judge({"x": 1}, {modelselect.QUESTION_NAME: question},
                                        model="gemma4:e4b",
                                        request=lambda b: _ollama_response({"C": 0.9, "A": 0.1}))
        answer = answers[modelselect.QUESTION_NAME]
        self.assertEqual(answer["choice"], modelselect.OTHER_KEY)
        self.assertEqual(answer["method"], judge.METHOD_LOGPROBS)


if __name__ == "__main__":
    unittest.main()
