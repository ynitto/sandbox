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


# ─────────────────────────────────────────────
#  ステートの中: 判定だけのステート / 出力契約の正規化 / 検査失敗の選別
# ─────────────────────────────────────────────
JUDGE_STATE = textwrap.dedent("""
    name: triage
    initial_state: classify
    states:
      classify:
        judge:
          question: "このイシューの種類はどれか"
          choices:
            BUG: "動作の不具合の報告"
            FEATURE: "新しい機能の要望"
            QUESTION: "使い方の質問"
        output_key: classification
      bug:
        terminal: true
      feature:
        terminal: true
      question:
        terminal: true
      ask:
        terminal: true
    transitions:
      - from: classify
        to: bug
        condition_rule: "startswith:classification:BUG"
        priority: 1
      - from: classify
        to: feature
        condition_rule: "startswith:classification:FEATURE"
        priority: 2
      - from: classify
        to: question
        condition_rule: "startswith:classification:QUESTION"
        priority: 3
      - from: classify
        to: ask
        priority: 4
""")


def _state_answer(picked, confidence=0.9):
    return {judge_bridge.STATE_JUDGE_QUESTION: {
        "type": "choice", "choice": picked, "confidence": confidence, "coverage": 0.95,
        "method": "logprobs", "probabilities": {picked: confidence}}}


class JudgeStateSpecTests(unittest.TestCase):
    def test_normalizes_and_derives_the_contract(self):
        spec = judge_bridge.normalize_judge_state(
            {"question": "種類は", "choices": {"BUG": "不具合", "FEATURE": "要望"}},
            default_input="{{input}}")
        self.assertEqual(spec["choices"], [("BUG", "不具合"), ("FEATURE", "要望")])
        self.assertEqual(spec["unsure"], "UNSURE")
        self.assertEqual(spec["input"], "{{input}}")
        self.assertEqual(judge_bridge.judge_state_validator(spec), "startswith:BUG,FEATURE,UNSURE")
        q = judge_bridge.judge_state_question(spec)[judge_bridge.STATE_JUDGE_QUESTION]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["criteria"], {"BUG": "不具合", "FEATURE": "要望"})
        self.assertTrue(q["other"])

    def test_rejects_broken_declarations(self):
        for bad in ({"choices": {"A": "", "B": ""}},                       # 問い無し
                    {"question": "q", "choices": {"A": ""}},               # 1 択
                    {"question": "q", "choices": {"A B": "", "C": ""}},    # 空白入りのキー
                    {"question": "q", "choices": {"A": "", "UNSURE": ""}}, # unsure と重複
                    "just a string"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                judge_bridge.normalize_judge_state(bad, default_input="x")
        self.assertIsNone(judge_bridge.normalize_judge_state(None, default_input="x"))

    def test_fallback_action_is_short_and_asks_for_one_word(self):
        spec = judge_bridge.normalize_judge_state(
            {"question": "種類は", "choices": {"BUG": "不具合", "FEATURE": "要望"}},
            default_input="{{input}}")
        text = judge_bridge.judge_state_fallback_action(spec)
        self.assertIn("{{input}}", text)
        self.assertIn("- BUG: 不具合", text)
        self.assertIn("- UNSURE", text)
        self.assertIn("exactly one choice key", text)
        self.assertLess(len(text), 400, "生成経路でも短い（本文も理由も書かせない）")

    def test_answers_map_to_a_key_or_unsure(self):
        spec = judge_bridge.normalize_judge_state(
            {"question": "q", "choices": {"BUG": "", "FEATURE": ""}, "min_confidence": 0.7},
            default_input="x")
        self.assertEqual(judge_bridge.judge_state_output(spec, _state_answer("BUG")), "BUG")
        self.assertEqual(judge_bridge.judge_state_output(spec, _state_answer("other")), "UNSURE")
        self.assertEqual(judge_bridge.judge_state_output(spec, _state_answer("BUG", 0.5)), "UNSURE",
                         "確度不足は最頻に倒さず unsure")
        self.assertIsNone(judge_bridge.judge_state_output(spec, None))
        self.assertIsNone(judge_bridge.judge_state_output(spec, _state_answer("NOPE")))


class ContractNormalizationTests(unittest.TestCase):
    P = ["APPROVED", "NEEDS_REVISION", "REJECTED"]

    def test_already_valid_is_unchanged(self):
        self.assertEqual(judge_bridge.normalize_contract_line("APPROVED\n理由", self.P), "APPROVED\n理由")

    def test_word_inside_the_first_line_moves_to_the_front(self):
        self.assertEqual(judge_bridge.normalize_contract_line("結論: APPROVED。問題なし\n詳細", self.P),
                         "APPROVED\n詳細")
        self.assertEqual(judge_bridge.normalize_contract_line("verdict is needs_revision", self.P),
                         "NEEDS_REVISION")

    def test_longer_word_wins_and_substrings_do_not_match(self):
        self.assertEqual(judge_bridge.normalize_contract_line("result: PASSED all", ["PASS", "PASSED"]),
                         "PASSED")
        self.assertIsNone(judge_bridge.normalize_contract_line("it is PASSING by", ["PASS"]))

    def test_a_later_line_starting_with_the_word_is_promoted(self):
        self.assertEqual(judge_bridge.normalize_contract_line("レビュー結果です。\nRejected: 根拠が無い\nx", self.P),
                         "REJECTED: 根拠が無い\nx")

    def test_unfixable_is_none(self):
        self.assertIsNone(judge_bridge.normalize_contract_line("承認します", self.P))
        self.assertIsNone(judge_bridge.normalize_contract_line("", self.P))

    def test_judge_fills_the_word_only_with_confidence(self):
        q = judge_bridge.contract_question(self.P)[judge_bridge.CONTRACT_QUESTION]
        self.assertEqual(sorted(q["criteria"]), sorted(self.P))
        ok = {judge_bridge.CONTRACT_QUESTION: {"choice": "APPROVED", "confidence": 0.9}}
        self.assertEqual(judge_bridge.contract_from_answers("承認します", self.P, ok), "APPROVED\n承認します")
        weak = {judge_bridge.CONTRACT_QUESTION: {"choice": "APPROVED", "confidence": 0.4}}
        self.assertIsNone(judge_bridge.contract_from_answers("承認します", self.P, weak))
        other = {judge_bridge.CONTRACT_QUESTION: {"choice": "other", "confidence": 0.9}}
        self.assertIsNone(judge_bridge.contract_from_answers("承認します", self.P, other))


class CheckTriageTests(unittest.TestCase):
    def test_environment_failures_are_recognized(self):
        for text in ("bash: pytest: command not found", "ModuleNotFoundError: No module named 'x'",
                     "検査コマンドを実行できません: [Errno 2] …", "EACCES: permission denied"):
            self.assertIsNotNone(judge_bridge.check_failure_environment(text), text)
        self.assertIsNone(judge_bridge.check_failure_environment(
            "FAILED tests/test_x.py::test_a - AssertionError: 1 != 2"))

    def test_verdict_stops_only_on_a_confident_no(self):
        q = judge_bridge.CHECK_TRIAGE_QUESTION
        self.assertIs(judge_bridge.check_triage_verdict({q: {"value": False, "confidence": 0.9}}), False)
        self.assertIs(judge_bridge.check_triage_verdict({q: {"value": False, "confidence": 0.6}}), True)
        self.assertIs(judge_bridge.check_triage_verdict({q: {"value": True, "confidence": 0.9}}), True)
        self.assertIsNone(judge_bridge.check_triage_verdict(None))


class JudgeStateEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sm-judge-state-")
        self.addCleanup(self._tmp.cleanup)
        self.workflow = Path(self._tmp.name, "workflow.yaml")
        self.workflow.write_text(JUDGE_STATE, encoding="utf-8")

    def _run(self, judge, llm_answers, input_text="ログインで 500 が出る"):
        prompts = []

        async def llm_fn(prompt):
            prompts.append(prompt)
            return llm_answers.pop(0)

        engine = StateMachineEngine(llm_fn=llm_fn, judge=judge)
        result = asyncio.run(engine.run(load_workflow(self.workflow), input_text=input_text))
        return result, prompts

    def test_judge_state_needs_no_generation_at_all(self):
        judge = FakeJudge(_state_answer("BUG"))
        result, prompts = self._run(judge, [])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.final_state, "bug")
        self.assertEqual(prompts, [], "アクションも遷移も LLM を呼ばない")
        state_text, questions = judge.calls[0]
        self.assertIn("ログインで 500 が出る", state_text)
        self.assertEqual(questions[judge_bridge.STATE_JUDGE_QUESTION]["type"], "choice")
        self.assertEqual(result.context["classification"], "BUG")

    def test_other_becomes_unsure_and_falls_through(self):
        result, prompts = self._run(FakeJudge(_state_answer("other")), [])
        self.assertEqual(result.context["classification"], "UNSURE")
        self.assertEqual(result.final_state, "ask")

    def test_without_judge_a_short_one_word_generation_runs(self):
        result, prompts = self._run(None, ["FEATURE"])
        self.assertEqual(result.final_state, "feature")
        self.assertEqual(len(prompts), 1)
        self.assertIn("ログインで 500 が出る", prompts[0])
        self.assertIn("exactly one choice key", prompts[0])
        self.assertLess(len(prompts[0]), 500)

    def test_without_judge_a_chatty_answer_is_normalized_not_regenerated(self):
        result, prompts = self._run(None, ["I think this is a FEATURE request."])
        self.assertEqual(result.final_state, "feature")
        self.assertEqual(len(prompts), 1, "契約の語が本文にあれば再生成しない")

    def test_judge_undecided_falls_back_to_generation(self):
        result, prompts = self._run(FakeJudge(None), ["QUESTION"])
        self.assertEqual(result.final_state, "question")
        self.assertEqual(len(prompts), 1)

    def test_an_explicit_action_becomes_the_fallback_prompt(self):
        """action を併記すると、判定 AI が無いときの生成用プロンプトになる（判定 AI は先に使う）。"""
        self.workflow.write_text(JUDGE_STATE.replace('    output_key: classification',
                                                     '    output_key: classification\n    action: "種類を 1 語で: {{input}}"'),
                                 encoding="utf-8")
        from scripts.engine import validate_workflow
        wf = load_workflow(self.workflow)
        self.assertEqual(validate_workflow(wf), [])
        self.assertEqual(wf.states["classify"].action, "種類を 1 語で: {{input}}")
        self.assertEqual(wf.states["classify"].output_validator, "startswith:BUG,FEATURE,QUESTION,UNSURE")
        result, prompts = self._run(FakeJudge(_state_answer("BUG")), [])
        self.assertEqual(result.final_state, "bug")
        self.assertEqual(prompts, [])
        result, prompts = self._run(None, ["BUG"])
        self.assertEqual(prompts, ["種類を 1 語で: ログインで 500 が出る"])

    def test_state_judge_flag_exposes_the_spec(self):
        proc = subprocess.run([sys.executable, str(NEXT_STATE), str(self.workflow),
                               "--state", "classify", "--state-judge"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["judge"]["choices"]["BUG"], "動作の不具合の報告")
        self.assertEqual(data["judge"]["input"], "{{input}}")
        self.assertEqual(data["output_validator"], "startswith:BUG,FEATURE,QUESTION,UNSURE")
        self.assertIn("exactly one choice key", data["fallback_action"])
        self.assertEqual(data["question"][judge_bridge.STATE_JUDGE_QUESTION]["type"], "choice")
        proc = subprocess.run([sys.executable, str(NEXT_STATE), str(self.workflow),
                               "--state", "bug", "--state-judge"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertIsNone(json.loads(proc.stdout)["judge"])


class CheckTriageEngineTests(unittest.TestCase):
    """検査が落ちた後、やり直しても直らない失敗には再投入を積まない。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sm-triage-")
        self.addCleanup(self._tmp.cleanup)

    def _workflow(self, check_script: str):
        script = Path(self._tmp.name, "check.py")
        script.write_text(check_script, encoding="utf-8")
        wf = Path(self._tmp.name, "workflow.yaml")
        wf.write_text(textwrap.dedent(f"""
            name: gated
            initial_state: work
            states:
              work:
                action: "作業せよ"
                check: {json.dumps([sys.executable, str(script)])}
                check_retries: 3
                check_on_exhausted: continue
              done:
                terminal: true
            transitions:
              - from: work
                to: done
        """), encoding="utf-8")
        return wf

    def _run(self, wf, judge=None):
        calls = []

        async def llm_fn(prompt):
            calls.append(prompt)
            return "OK"

        engine = StateMachineEngine(llm_fn=llm_fn, judge=judge)
        return asyncio.run(engine.run(load_workflow(wf))), calls

    def test_environment_failure_stops_retries_without_judge(self):
        wf = self._workflow("import sys\nprint('bash: pytest: command not found', file=sys.stderr)\nsys.exit(127)\n")
        result, calls = self._run(wf)
        self.assertEqual(len(calls), 1, "環境の失敗にはやり直しを積まない")
        self.assertIn("環境の失敗", result.steps[0]["check"]["triage"])

    def test_code_failure_retries_as_before(self):
        wf = self._workflow("import sys\nprint('FAILED tests/test_x.py::t - AssertionError')\nsys.exit(1)\n")
        result, calls = self._run(wf)
        self.assertEqual(len(calls), 4, "コードの失敗は従来どおり check_retries まで再投入")

    def test_judge_can_stop_retries_only_when_confident(self):
        wf = self._workflow("import sys\nprint('some odd failure')\nsys.exit(1)\n")
        stop = FakeJudge({judge_bridge.CHECK_TRIAGE_QUESTION: {"value": False, "confidence": 0.95}})
        result, calls = self._run(wf, judge=stop)
        self.assertEqual(len(calls), 1)
        weak = FakeJudge({judge_bridge.CHECK_TRIAGE_QUESTION: {"value": False, "confidence": 0.5}})
        result, calls = self._run(wf, judge=weak)
        self.assertEqual(len(calls), 4)
