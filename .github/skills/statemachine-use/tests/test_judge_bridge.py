"""遷移条件の判定を agent-herd judge へ橋渡しする契約（scripts/judge_bridge.py と、その利用側）。

縛るのは 4 点:
1. 問いの組み方 — 全候補に `outcome` があれば choice 1 問、無ければ条件ごとの boolean。
2. 答えの読み方 — choice は選ばれた候補だけ真、`other` は全部偽、abstained は「決めていない」。
3. next_state.py — `--auto-eval` が `outcome` と `judge_questions` を出し、`--judge-answers` で
   遷移先が確定する。judge が無い経路（`--eval`）は従来どおり。
4. engine — judge があれば 1 問で選び、judge が決めなければ LLM の YES/NO に倒れる。
   agent-herd の有無で定義を書き換えなくてよい。

pytest でも `python -m unittest` でも走る（fixture を使わない）。
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import judge_bridge  # noqa: E402
from scripts.engine import StateMachineEngine, load_workflow  # noqa: E402

NEXT_STATE = ROOT / "scripts" / "next_state.py"

WORKFLOW = textwrap.dedent("""
    name: review
    initial_state: review
    states:
      review:
        action: "レビューせよ"
      approve:
        terminal: true
      revise:
        terminal: true
      ask:
        terminal: true
    transitions:
      - from: review
        to: approve
        outcome: "承認できる"
        priority: 1
      - from: review
        to: revise
        outcome: "直すべき指摘がある"
        priority: 2
      - from: review
        to: ask
        outcome: "判断できない"
        priority: 3
""")

MIXED = textwrap.dedent("""
    name: mixed
    initial_state: classify
    states:
      classify: {}
      bug:
        terminal: true
      ask:
        terminal: true
    transitions:
      - from: classify
        to: bug
        condition_rule: "startswith:last_output:BUG"
        priority: 1
      - from: classify
        to: ask
        condition: "判断できない"
        priority: 2
""")


def _pending(*outcomes, conditions=()):
    out = [{"index": i, "to": f"s{i}", "condition": "", "description": "", "outcome": o}
           for i, o in enumerate(outcomes)]
    for i, text in enumerate(conditions, start=len(out)):
        out.append({"index": i, "to": f"s{i}", "condition": text, "description": "",
                    "outcome": ""})
    return out


def _choice(picked, **probabilities):
    return {"answers": {judge_bridge.OUTCOME_QUESTION: {
        "type": "choice", "choice": picked, "probabilities": probabilities,
        "confidence": max(probabilities.values()), "coverage": 0.97, "method": "logprobs"}},
        "abstained": []}


class QuestionShapeTests(unittest.TestCase):
    def test_all_outcomes_become_one_choice(self):
        questions = judge_bridge.judge_questions(_pending("A", "B", "C"))
        self.assertEqual(list(questions), [judge_bridge.OUTCOME_QUESTION])
        q = questions[judge_bridge.OUTCOME_QUESTION]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["criteria"], {"0": "A", "1": "B", "2": "C"})
        self.assertTrue(q["other"], "「どれでもない」を明示の選択肢にする")

    def test_missing_or_duplicate_outcome_falls_back_to_booleans(self):
        questions = judge_bridge.judge_questions(_pending("A", conditions=["最後の出力が X"]))
        self.assertEqual(sorted(questions), ["0", "1"])
        self.assertTrue(all(q["type"] == "boolean" for q in questions.values()))
        self.assertIn("A", questions["0"]["instructions"], "outcome は条件文の代わりになる")
        dup = judge_bridge.judge_questions(_pending("A", "A"))
        self.assertTrue(all(q["type"] == "boolean" for q in dup.values()))

    def test_single_candidate_is_a_boolean(self):
        questions = judge_bridge.judge_questions(_pending("A"))
        self.assertEqual(questions["0"]["type"], "boolean")


class AnswerReadingTests(unittest.TestCase):
    def setUp(self):
        self.questions = judge_bridge.judge_questions(_pending("A", "B", "C"))

    def test_choice_marks_only_the_picked_candidate(self):
        evals = judge_bridge.evals_from_judge_output(
            self.questions, _choice("1", **{"0": .1, "1": .8, "2": .05, "other": .05}))
        self.assertEqual(evals, {"0": False, "1": True, "2": False})

    def test_other_marks_none(self):
        evals = judge_bridge.evals_from_judge_output(
            self.questions, _choice("other", **{"0": .1, "1": .1, "2": .1, "other": .7}))
        self.assertEqual(evals, {"0": False, "1": False, "2": False})

    def test_abstained_is_not_an_answer(self):
        data = _choice("1", **{"0": .5, "1": .5})
        data["abstained"] = [judge_bridge.OUTCOME_QUESTION]
        self.assertIsNone(judge_bridge.evals_from_judge_output(self.questions, data))

    def test_booleans_map_by_index(self):
        questions = judge_bridge.judge_questions(_pending(conditions=["p", "q"]))
        answers = {"0": {"type": "boolean", "value": True}, "1": {"type": "boolean", "value": False}}
        self.assertEqual(judge_bridge.evals_from_answers(questions, answers),
                         {"0": True, "1": False})


class NextStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sm-judge-")
        self.addCleanup(self._tmp.cleanup)
        self.workflow = Path(self._tmp.name, "workflow.yaml")
        self.workflow.write_text(WORKFLOW, encoding="utf-8")

    def _run(self, *args, expect=0):
        proc = subprocess.run([sys.executable, str(NEXT_STATE), str(self.workflow), *args],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, expect, proc.stderr)
        return proc

    def test_auto_eval_lists_outcomes_and_a_ready_question(self):
        listed = json.loads(self._run("--state", "review", "--auto-eval",
                                      "--context", '{"last_output": "MINOR: typo"}').stdout)
        self.assertIsNone(listed["resolved"])
        self.assertEqual([c["outcome"] for c in listed["conditions"]],
                         ["承認できる", "直すべき指摘がある", "判断できない"])
        self.assertTrue(all(c["needs_llm_eval"] for c in listed["conditions"]),
                        "outcome だけの遷移は無条件ではない")
        q = listed["judge_questions"][judge_bridge.OUTCOME_QUESTION]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["criteria"]["1"], "直すべき指摘がある")

    def test_judge_answers_resolve_the_transition(self):
        answers = json.dumps(_choice("1", **{"0": .1, "1": .8, "2": .05, "other": .05}),
                             ensure_ascii=False)
        out = self._run("--state", "review", "--judge-answers", answers,
                        "--context", '{"last_output": "MINOR: typo"}').stdout.strip()
        self.assertEqual(out, "revise")

    def test_other_yields_none(self):
        answers = json.dumps(_choice("other", **{"0": .1, "1": .1, "2": .1, "other": .7}))
        out = self._run("--state", "review", "--judge-answers", answers,
                        "--context", '{"last_output": "???"}').stdout.strip()
        self.assertEqual(out, "NONE")

    def test_abstained_stops_with_exit_3(self):
        data = _choice("1", **{"0": .5, "1": .5})
        data["abstained"] = [judge_bridge.OUTCOME_QUESTION]
        proc = self._run("--state", "review", "--judge-answers", json.dumps(data),
                         "--context", '{"last_output": "x"}', expect=3)
        self.assertIn("確度不足", proc.stderr)

    def test_eval_path_is_unchanged(self):
        out = self._run("--state", "review", "--eval", '{"2": true}',
                        "--context", '{"last_output": "x"}').stdout.strip()
        self.assertEqual(out, "ask")

    def test_rule_resolved_candidates_are_left_out_of_the_question(self):
        self.workflow.write_text(MIXED, encoding="utf-8")
        listed = json.loads(self._run("--state", "classify", "--auto-eval",
                                      "--context", '{"last_output": "???"}').stdout)
        self.assertEqual(list(listed["judge_questions"]), ["1"])
        self.assertEqual(listed["judge_questions"]["1"]["type"], "boolean")
        answers = json.dumps({"answers": {"1": {"type": "boolean", "value": True}},
                              "abstained": []})
        out = self._run("--state", "classify", "--judge-answers", answers,
                        "--context", '{"last_output": "???"}').stdout.strip()
        self.assertEqual(out, "ask")


class FakeJudge:
    def __init__(self, answers=None):
        self.answers = answers
        self.calls = []

    def evaluate(self, state_text, questions):
        self.calls.append((state_text, questions))
        return self.answers


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sm-judge-engine-")
        self.addCleanup(self._tmp.cleanup)
        self.workflow = Path(self._tmp.name, "workflow.yaml")
        self.workflow.write_text(WORKFLOW, encoding="utf-8")

    def _run(self, judge, llm_answers):
        prompts = []

        async def llm_fn(prompt):
            prompts.append(prompt)
            return llm_answers.pop(0)

        engine = StateMachineEngine(llm_fn=llm_fn, judge=judge)
        result = asyncio.run(engine.run(load_workflow(self.workflow), input_text="doc"))
        return result, prompts

    def test_judge_picks_the_transition_in_one_question(self):
        judge = FakeJudge(_choice("1", **{"0": .1, "1": .8, "2": .05, "other": .05})["answers"])
        result, prompts = self._run(judge, ["MINOR: typo"])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.final_state, "revise")
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(len(prompts), 1, "LLM を呼んだのはアクションの 1 回だけ（条件評価は judge）")
        state_text, questions = judge.calls[0]
        self.assertIn("MINOR: typo", state_text)
        self.assertEqual(questions[judge_bridge.OUTCOME_QUESTION]["type"], "choice")

    def test_judge_undecided_falls_back_to_yes_no(self):
        result, prompts = self._run(FakeJudge(None), ["MINOR: typo", "NO", "YES"])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.final_state, "revise")
        self.assertEqual(len(prompts), 3)
        self.assertIn("直すべき指摘がある", prompts[2], "outcome が条件文として LLM に渡る")

    def test_without_judge_the_definition_still_runs(self):
        result, prompts = self._run(None, ["MINOR: typo", "NO", "NO", "YES"])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.final_state, "ask")


class ResolveJudgeTests(unittest.TestCase):
    """agent-herd の有無と設定で、判定 AI を使うかどうかを決める。"""

    def _run_factory(self, check_rc=0, check_out='{"mode": "pinned", "model": "gemma4:e4b"}'):
        def run(argv, **kwargs):
            self.assertEqual(argv[1:], ["config", "--check", "judge"])
            return subprocess.CompletedProcess(argv, check_rc, stdout=check_out, stderr="")
        return run

    def test_auto_uses_judge_only_when_a_model_is_pinned(self):
        with mock.patch.object(judge_bridge, "herd_path", return_value="/usr/bin/agent-herd"):
            client = judge_bridge.resolve_judge("auto", run=self._run_factory())
            self.assertIsNotNone(client)
            self.assertEqual(client.model, "gemma4:e4b")
            self.assertIsNone(judge_bridge.resolve_judge(
                "auto", run=self._run_factory(check_rc=1, check_out='{"mode": "auto"}')))

    def test_herd_forces_and_off_never(self):
        with mock.patch.object(judge_bridge, "herd_path", return_value="/usr/bin/agent-herd"):
            client = judge_bridge.resolve_judge("herd", run=lambda *a, **k: self.fail("no probe"))
            self.assertIsNotNone(client)
            self.assertIsNone(client.model)
            self.assertIsNone(judge_bridge.resolve_judge("off"))

    def test_no_herd_means_no_judge(self):
        with mock.patch.object(judge_bridge, "herd_path", return_value=None):
            self.assertIsNone(judge_bridge.resolve_judge("auto"))
            self.assertIsNone(judge_bridge.resolve_judge("herd"))


class JudgeClientTests(unittest.TestCase):
    def _client(self, rc, stdout, stderr=""):
        seen = []

        def run(argv, **kwargs):
            seen.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)
        return judge_bridge.JudgeClient("/usr/bin/agent-herd", model="gemma4:e4b", run=run), seen

    def test_answers_are_returned_and_state_goes_on_stdin(self):
        client, seen = self._client(0, json.dumps(_choice("0", **{"0": .9, "other": .1})))
        answers = client.evaluate("Last output: OK", {"q": {"type": "boolean", "instructions": "x"}})
        self.assertIn(judge_bridge.OUTCOME_QUESTION, answers)
        argv, kwargs = seen[0]
        self.assertEqual(argv[:3], ["/usr/bin/agent-herd", "judge", "--questions"])
        self.assertIn("--model", argv)
        self.assertEqual(kwargs["input"], "Last output: OK")

    def test_abstained_is_none_but_keeps_the_client(self):
        client, _ = self._client(1, json.dumps({"answers": {}, "abstained": ["q"]}))
        self.assertIsNone(client.evaluate("s", {"q": {"type": "boolean", "instructions": "x"}}))
        self.assertIsNone(client.disabled_reason)

    def test_failure_disables_further_calls(self):
        client, seen = self._client(1, "", stderr="[agent-error:env] ollama に接続できません")
        q = {"q": {"type": "boolean", "instructions": "x"}}
        self.assertIsNone(client.evaluate("s", q))
        self.assertIn("接続できません", client.disabled_reason)
        self.assertIsNone(client.evaluate("s", q))
        self.assertEqual(len(seen), 1, "落ちた後は呼ばない")


if __name__ == "__main__":
    unittest.main()
