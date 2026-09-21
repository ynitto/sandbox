"""agentcore.route と `agent-herd route` の契約。

縛るのは 4 つ:

1. **問いは 1 基準 1 問で、候補の無い種類は組まない。** readonly なら `handling` を訊かない。
2. **順は jev → judge。決定的な段は無く、決めなければ `stage` が None で終了コード 1。**
   確度不足・`other`・本文読み（method text）は決めたことにしない。
3. **`hold` は handling と流用先の両方が hold 下限以上のときだけ真。**
4. **段の試行は `modelselect.ask_stages` を共有する**（attempts の形が select と同じ）。
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

from agentcore import herdcli, herdconfig, modelselect, route  # noqa: E402

CANDIDATES = {
    "tasks": [{"id": "daily-report", "name": "日報", "description": "前日の commit から日報を書く"},
              {"id": "lint", "name": "静的検査", "description": "lint を回して直す"}],
    "flows": [{"id": "release-check", "name": "リリース前点検", "description": "点検して納品する"}],
    "skills": [{"name": "api-designer", "description": "REST API の設計"},
               {"name": "self-checking", "description": "成果物の検証"}],
    "context": {"repo": "sandbox", "attachments": [], "readonly": False},
}


def _logprobs(top: "dict[str, float]") -> dict:
    """ollama `/api/chat` の応答（logprobs つき）。ラベル→確率。"""
    first = next(iter(top))
    return {"message": {"role": "assistant", "content": first},
            "logprobs": [{"token": first, "logprob": math.log(top[first]),
                          "top_logprobs": [{"token": t, "logprob": math.log(p)}
                                           for t, p in top.items()]}],
            "prompt_eval_count": 40, "eval_count": 1}


def _judge_by_question(table: "dict[str, dict]"):
    """問いごとに分布を返す judge の偽サーバ。問いはプロンプト末尾の `Question:` 行で見分ける。"""
    def request(payload: dict) -> dict:
        prompt = payload["messages"][-1]["content"]
        for needle, top in table.items():
            if needle in prompt:
                return _logprobs(top)
        raise AssertionError(f"想定外の問い: {prompt[-200:]!r}")
    return request


# 問いの見分け方（build_questions の instructions の一部）
Q_HANDLING = "How should this request be handled?"
Q_TASK = "which one?\nOptions:\nA. daily-report"
Q_FLOW = "workflow 'リリース前点検"
Q_SKILL_API = "skill 'api-designer"
Q_SKILL_CHECK = "skill 'self-checking"
Q_ROUTINE = "recurring shape"

# handling の選択肢は answer / converse / task / flow / other の順（A〜E）
TASK_ROUTE = {
    Q_HANDLING: {"C": 0.82, "B": 0.1, "A": 0.05, "D": 0.02, "E": 0.01},
    Q_TASK: {"A": 0.77, "B": 0.13, "C": 0.1},
    Q_FLOW: {"B": 0.9, "A": 0.1},
    Q_SKILL_API: {"A": 0.71, "B": 0.29},
    Q_SKILL_CHECK: {"B": 0.8, "A": 0.2},
    Q_ROUTINE: {"A": 0.66, "B": 0.34},
}


class IsolatedHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-herd-route-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(modelselect.JEV_API_KEY_ENV, None)


class QuestionShapeTests(IsolatedHome):
    def test_questions_follow_the_candidates(self):
        questions = route.build_questions(route.normalize_candidates(CANDIDATES))
        self.assertEqual(list(questions), ["handling", "task", "flow", "skill:api-designer",
                                           "skill:self-checking", "routine"])
        self.assertEqual(list(questions["handling"]["criteria"]), ["answer", "converse", "task", "flow"])
        self.assertTrue(questions["handling"]["other"])
        self.assertEqual(questions["skill:api-designer"]["type"], "boolean")
        self.assertEqual(questions["task"]["type"], "choice")
        self.assertEqual(questions["flow"], {"type": "boolean", "candidate": "release-check",
                                             "instructions": questions["flow"]["instructions"]},
                         "候補 1 件は boolean で訊く")

    def test_no_tasks_drops_the_task_question_and_option(self):
        questions = route.build_questions(route.normalize_candidates({"skills": []}))
        self.assertEqual(list(questions), ["handling", "routine"])
        self.assertEqual(list(questions["handling"]["criteria"]), ["answer", "converse"])

    def test_readonly_skips_handling(self):
        questions = route.build_questions(route.normalize_candidates(
            {"skills": [{"name": "x"}], "context": {"readonly": True}}))
        self.assertEqual(list(questions), ["skill:x", "routine"])

    def test_candidates_are_checked(self):
        with self.assertRaises(route.RouteError):
            route.normalize_candidates([])
        with self.assertRaises(route.RouteError):
            route.normalize_candidates({"tasks": [{"name": "no id"}]})
        with self.assertRaises(route.RouteError):
            route.normalize_candidates({"tasks": [{"id": f"t{i}"} for i in range(26)]})
        dedup = route.normalize_candidates({"skills": [{"name": "a"}, {"name": "a"}]})
        self.assertEqual([s["id"] for s in dedup["skills"]], ["a"])

    def test_state_puts_the_request_first_and_cuts_the_excerpt(self):
        state = route.build_state("x" * 2000, route.normalize_candidates(CANDIDATES))
        self.assertEqual(list(state)[0], "request")
        self.assertEqual(len(state["request"]["excerpt"]), modelselect.EXCERPT_CHARS + 1)
        self.assertEqual(state["request"]["chars"], 2000)


class StageTests(IsolatedHome):
    JEV = {"enabled": True, "api_key": "k", "endpoint": "http://jev", "model": "jev-latest"}

    def test_judge_routes_to_a_task_and_holds(self):
        result = route.route("前月分の日報をまとめて", CANDIDATES, judge_model="gemma4:e4b",
                             judge_request=_judge_by_question(TASK_ROUTE))
        self.assertEqual(result["stage"], "judge")
        self.assertEqual(result["handling"]["choice"], "task")
        self.assertEqual(result["task"]["choice"], "daily-report")
        self.assertIsNone(result["flow"], "flow は no")
        self.assertEqual([s["name"] for s in result["skills"]], ["api-designer"])
        self.assertTrue(result["routine"]["value"])
        self.assertTrue(result["hold"])
        self.assertEqual(result["abstained"], [], "決めた上での no は棄権ではない")
        self.assertEqual([a["stage"] for a in result["attempts"]], ["jev", "judge"])
        self.assertEqual(result["attempts"][0]["outcome"], "not-configured")
        self.assertEqual(result["attempts"][1]["outcome"], "chosen")
        self.assertGreater(result["usage"]["tokens_in"], 0)

    def test_hold_needs_both_confidences(self):
        low_task = dict(TASK_ROUTE, **{Q_TASK: {"A": 0.65, "B": 0.2, "C": 0.15}})
        result = route.route("x", CANDIDATES, judge_model="m", judge_request=_judge_by_question(low_task))
        self.assertEqual(result["handling"]["choice"], "task")
        self.assertEqual(result["task"]["choice"], "daily-report", "下限 0.6 は超えるので報告はする")
        self.assertFalse(result["hold"], "0.75 に届かないので会話は止めない")
        loose = route.route("x", CANDIDATES, judge_model="m", hold_min_confidence=0.6,
                            judge_request=_judge_by_question(low_task))
        self.assertTrue(loose["hold"])

    def test_low_confidence_or_other_or_text_is_undecided(self):
        low = dict(TASK_ROUTE, **{Q_HANDLING: {"C": 0.5, "B": 0.4, "A": 0.1}})
        result = route.route("x", CANDIDATES, judge_model="m", judge_request=_judge_by_question(low))
        self.assertIsNone(result["stage"])
        self.assertIsNone(result["handling"])
        self.assertEqual(result["attempts"][-1]["outcome"], "low-confidence")
        self.assertEqual(result["attempts"][-1]["handling"], "task", "決めなくても生の答えは記録に残す")
        self.assertEqual(result["attempts"][-1]["confidence"], 0.5)

        other = dict(TASK_ROUTE, **{Q_HANDLING: {"E": 0.9, "B": 0.1}})
        result = route.route("x", CANDIDATES, judge_model="m", judge_request=_judge_by_question(other))
        self.assertIsNone(result["stage"])
        self.assertEqual(result["attempts"][-1]["outcome"], "none-of-them")

        no_logprobs = lambda payload: {"message": {"content": "A"}}  # noqa: E731
        result = route.route("x", CANDIDATES, judge_model="m", judge_request=no_logprobs)
        self.assertIsNone(result["stage"], "本文読みは確度が無いので決めない")
        self.assertEqual(result["attempts"][-1]["outcome"], "no-confidence")

    def test_judge_off_means_no_stage_and_no_request(self):
        herdconfig.set_value("judge.model", "off")
        result = route.route("x", CANDIDATES,
                             judge_request=lambda p: self.fail("judge は呼ばれない"))
        self.assertIsNone(result["stage"])
        self.assertEqual([a["outcome"] for a in result["attempts"]], ["not-configured", "not-available"])

    def test_jev_answers_every_question_in_one_call(self):
        calls = []

        def jev(body):
            calls.append(body)
            self.assertEqual(list(body["questions"]), ["handling", "task", "flow", "skill:api-designer",
                                                       "skill:self-checking", "routine"])
            self.assertEqual(body["questions"]["skill:api-designer"], {
                "type": "boolean", "instructions": body["questions"]["skill:api-designer"]["instructions"]})
            self.assertIn("none", body["questions"]["handling"]["criteria"])
            self.assertEqual(body["questions"]["flow"]["type"], "boolean")
            return {"model": "jev-1", "usage": {"input_tokens": 300, "output_tokens": 20}, "answers": {
                "handling": {"type": "choice", "choice": "answer", "confidence": 0.9,
                             "probabilities": {"answer": 0.9, "converse": 0.1}},
                "task": {"type": "choice", "choice": "none", "confidence": 0.8, "probabilities": {"none": 0.8}},
                "flow": {"type": "boolean", "value": False, "probability": 0.1},
                "skill:api-designer": {"type": "boolean", "value": True, "probability": 0.7},
                "skill:self-checking": {"type": "boolean", "value": False, "probability": 0.2},
                "routine": {"type": "boolean", "value": False, "probability": 0.3},
            }}

        result = route.route("この関数は何をする？", CANDIDATES, jev_setting_override=self.JEV,
                             jev_request=jev, judge_request=lambda p: self.fail("jev で決まる"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stage"], "jev")
        self.assertEqual(result["handling"]["choice"], "answer")
        self.assertIsNone(result["task"])
        self.assertIsNone(result["flow"])
        self.assertEqual(result["abstained"], [])
        self.assertEqual(result["skills"], [{"name": "api-designer", "probability": 0.7}])
        self.assertEqual(result["routine"], {"value": False, "probability": 0.3, "confidence": 0.7})
        self.assertFalse(result["hold"])
        self.assertEqual(result["usage"], {"tokens_in": 300, "tokens_out": 20}, "1 往復分だけ数える")

    def test_single_candidate_yes_becomes_the_choice(self):
        yes_flow = dict(TASK_ROUTE, **{Q_FLOW: {"A": 0.9, "B": 0.1}})
        result = route.route("x", CANDIDATES, judge_model="m", judge_request=_judge_by_question(yes_flow))
        self.assertEqual(result["flow"], {"choice": "release-check", "confidence": 0.9,
                                          "probabilities": {"yes": 0.9, "no": 0.1}})

    def test_readonly_decides_without_handling(self):
        readonly = dict(CANDIDATES, context={"readonly": True})
        result = route.route("x", readonly, judge_model="m", judge_request=_judge_by_question(TASK_ROUTE))
        self.assertEqual(result["stage"], "judge")
        self.assertIsNone(result["handling"])
        self.assertEqual(result["attempts"][-1]["outcome"], "no-handling-question")

    def test_thresholds_come_from_the_config_file(self):
        herdconfig.set_value("route.min_confidence", 0.9)
        herdconfig.set_value("route.hold_min_confidence", 0.5)
        self.assertEqual(route.min_confidence_setting(), 0.9)
        self.assertEqual(route.hold_min_confidence_setting(), 0.5)
        herdconfig.unset_value("route.min_confidence")
        herdconfig.set_value("select.min_confidence", 0.3)
        self.assertEqual(route.min_confidence_setting(), 0.3, "省略時は select と同じ")
        self.assertEqual(herdconfig.describe()["route"], {"min_confidence": None,
                                                           "hold_min_confidence": 0.5, "error": None})
        with self.assertRaises(herdconfig.ConfigError):
            herdconfig.set_value("route.hold_min_confidence", "2")

    def test_empty_prompt_is_refused(self):
        with self.assertRaises(route.RouteError):
            route.route("  ", CANDIDATES)


class RouteCommandTests(IsolatedHome):
    def _run(self, argv, stdin="前月分の日報をまとめて", **kw):
        out, err = io.StringIO(), io.StringIO()
        rc = herdcli.cmd_route(argv, out=out, err=err, stdin=io.StringIO(stdin), **kw)
        return rc, out.getvalue(), err.getvalue()

    def test_decides_and_reports(self):
        path = self.home / "candidates.json"
        path.write_text(json.dumps(CANDIDATES), encoding="utf-8")
        herdconfig.set_value("judge.model", "gemma4:e4b")
        rc, out, err = self._run(["--candidates", str(path)],
                                 judge_request=_judge_by_question(TASK_ROUTE))
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["handling"]["choice"], "task")
        self.assertEqual(data["task"]["choice"], "daily-report")
        self.assertTrue(data["hold"])
        self.assertEqual(data["stage"], "judge")
        self.assertNotIn("state", data)
        self.assertIn("@agent-usage tokens_in=", err)

    def test_undecided_is_exit_1_and_json_carries_state(self):
        rc, out, _ = self._run(["--candidates", json.dumps(CANDIDATES), "--json", "--stages", "judge"],
                               judge_request=lambda p: {"message": {"content": "A"}})
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIsNone(data["stage"])
        self.assertEqual(data["state"]["request"]["excerpt"], "前月分の日報をまとめて")
        self.assertEqual(data["questions"][0], "handling")

    def test_argument_errors_are_exit_2(self):
        self.assertEqual(self._run([])[0], 2)
        self.assertEqual(self._run(["--candidates"])[0], 2)
        self.assertEqual(self._run(["--candidates", "{not json"])[0], 2)
        self.assertEqual(self._run(["--candidates", "[]"])[0], 2)
        self.assertEqual(self._run(["--candidates", "{}", "--stages", "audit"])[0], 2)
        self.assertEqual(self._run(["--candidates", "{}", "--hold-min-confidence", "2"])[0], 2)
        self.assertEqual(self._run(["--candidates", "{}", "--bogus"])[0], 2)
        self.assertEqual(self._run(["--candidates", "{}"], stdin="")[0], 2)

    def test_main_dispatches_route_and_help(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = herdcli.main(["route", "--help"], prog="agent-herd")
        self.assertEqual(rc, 0)
        self.assertIn("--candidates", out.getvalue())
        self.assertIn("route", herdcli.HELP)
        self.assertIn("route.hold_min_confidence", herdcli.CONFIG_HELP)


if __name__ == "__main__":
    unittest.main()
