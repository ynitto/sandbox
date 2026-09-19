"""agentcore.judge — 型付きの判断を確率つきで返す（System One 型）の契約を縛る。

背骨は 3 つ:

1. **文章を生成しない。** 答えは 1 トークン目の分布の読み出しで、`num_predict` は
   小さく、確率はラベルに落ちた質量の正規化。
2. **状態が先、問いが後。** 複数の問いが同じ接頭辞を共有する（キャッシュに乗る）。
3. **確率の出どころを隠さない。** logprobs が無ければ `method` が変わり、`coverage` が
   落ちる。黙って 1.0 を作らない。
"""
from __future__ import annotations

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import judge  # noqa: E402


def _lp(p: float) -> float:
    return math.log(p)


def _response(top: "dict[str, float]", *, content: str = "", usage=(10, 1)) -> dict:
    """ollama `/api/chat` の応答（logprobs つき）を 1 位置分だけ作る。"""
    return {
        "message": {"role": "assistant", "content": content or next(iter(top))},
        "logprobs": [{"token": next(iter(top)), "logprob": _lp(next(iter(top.values()))),
                      "top_logprobs": [{"token": t, "logprob": _lp(p)} for t, p in top.items()]}],
        "prompt_eval_count": usage[0], "eval_count": usage[1],
    }


ROUTE = {"type": "choice", "instructions": "Which team should handle this?",
         "criteria": {"billing": "Billing and refunds", "support": "Other requests"}}
URGENT = {"type": "boolean", "instructions": "Is this urgent?"}
SEVERITY = {"type": "score", "instructions": "How severe is it?",
            "criteria": ["low", "medium", "high"]}


class QuestionShapeTests(unittest.TestCase):
    def test_each_type_normalizes_to_lettered_options(self):
        q = judge.normalize_question("route", ROUTE)
        self.assertEqual([k for k, _ in q["options"]], ["billing", "support"])
        q = judge.normalize_question("urgent", URGENT)
        self.assertEqual([k for k, _ in q["options"]], ["yes", "no"])
        q = judge.normalize_question("sev", SEVERITY)
        self.assertEqual(q["values"], [0.0, 1.0, 2.0])

    def test_numeric_keys_become_score_values(self):
        q = judge.normalize_question("stars", {"type": "score", "instructions": "Rate",
                                               "criteria": {"1": "poor", "3": "ok", "5": "great"}})
        self.assertEqual(q["values"], [1.0, 3.0, 5.0])

    def test_other_is_appended_as_an_explicit_option(self):
        q = judge.normalize_question("route", dict(ROUTE, other="Neither team"))
        self.assertEqual(q["options"][-1], ("other", "Neither team"))
        self.assertTrue(q["has_other"])

    def test_broken_questions_are_listed_not_guessed(self):
        errors = judge.question_errors({
            "a": {"type": "pick", "instructions": "x", "criteria": {"1": "", "2": ""}},
            "b": {"type": "choice", "instructions": "x", "criteria": {"only": ""}},
            "c": {"type": "boolean"},
        })
        self.assertEqual(len(errors), 3)
        self.assertEqual(judge.question_errors({}), ["questions は 1 つ以上の問いを持つオブジェクトです"])


class PromptTests(unittest.TestCase):
    def test_state_comes_first_and_the_question_last(self):
        q = judge.normalize_question("route", ROUTE)
        prompt = judge.build_prompt("ticket: refund please", q)
        self.assertLess(prompt.index("ticket: refund please"), prompt.index("Question:"))
        self.assertIn("A. billing: Billing and refunds", prompt)
        self.assertIn("B. support: Other requests", prompt)
        self.assertTrue(prompt.endswith("Answer (one letter):"))

    def test_two_questions_share_the_same_prefix(self):
        """同じ状態への問いは接頭辞が一致する（ollama のキャッシュに乗る形）。"""
        state = judge.render_state({"ticket": "refund please", "age_days": 3})
        a = judge.build_prompt(state, judge.normalize_question("route", ROUTE))
        b = judge.build_prompt(state, judge.normalize_question("urgent", URGENT))
        common = os.path.commonprefix([a, b])
        self.assertIn(">>>", common)


class ReadoutTests(unittest.TestCase):
    def test_label_mass_is_normalized_and_leakage_is_coverage(self):
        read = judge.readout([{"token": " A", "logprob": _lp(0.6), "top_logprobs": [
            {"token": " A", "logprob": _lp(0.6)},
            {"token": "B", "logprob": _lp(0.2)},
            {"token": "The", "logprob": _lp(0.15)},
        ]}], 2)
        masses, coverage = read
        self.assertAlmostEqual(coverage, 0.8, places=6)
        self.assertAlmostEqual(masses[0] / sum(masses), 0.75, places=6)

    def test_the_position_with_the_most_label_mass_wins(self):
        """1 トークン目が改行でも、次の位置でラベルを読む。"""
        read = judge.readout([
            {"token": "\n", "logprob": _lp(0.9), "top_logprobs": [{"token": "\n", "logprob": _lp(0.9)}]},
            {"token": "B", "logprob": _lp(0.7), "top_logprobs": [{"token": "B", "logprob": _lp(0.7)},
                                                                 {"token": "A", "logprob": _lp(0.3)}]},
        ], 2)
        self.assertIsNotNone(read)
        self.assertAlmostEqual(read[1], 1.0, places=6)

    def test_no_label_anywhere_returns_none(self):
        self.assertIsNone(judge.readout([{"token": "yes", "logprob": -0.1,
                                          "top_logprobs": [{"token": "yes", "logprob": -0.1}]}], 2))
        self.assertIsNone(judge.readout("not-a-list", 2))


class EvaluateTests(unittest.TestCase):
    def test_choice_boolean_and_score_answers(self):
        sent = []

        def request(payload):
            sent.append(payload)
            prompt = payload["messages"][0]["content"]
            if "Which team" in prompt:
                return _response({"A": 0.7, "B": 0.2, " the": 0.05})
            if "urgent" in prompt:
                return _response({"B": 0.8, "A": 0.1})
            return _response({"C": 0.5, "B": 0.4, "A": 0.1})

        result = judge.evaluate("ticket: refund", {"route": ROUTE, "urgent": URGENT,
                                                   "sev": SEVERITY}, request=request)
        route, urgent, sev = (result["answers"][k] for k in ("route", "urgent", "sev"))
        self.assertEqual(route["choice"], "billing")
        self.assertAlmostEqual(route["probabilities"]["billing"], 0.7778, places=3)
        self.assertEqual(route["method"], "logprobs")
        self.assertAlmostEqual(route["coverage"], 0.9, places=3)
        self.assertFalse(urgent["value"])
        self.assertAlmostEqual(urgent["probability"], 0.1111, places=3)
        self.assertEqual(sev["bucket"], "high")
        self.assertAlmostEqual(sev["score"], 1.4, places=3)     # 0.1*0 + 0.4*1 + 0.5*2
        self.assertEqual(result["usage"], {"tokens_in": 30, "tokens_out": 3})
        # 文章を生成しない: 生成上限は小さく、logprobs を求め、温度 0。
        for payload in sent:
            self.assertTrue(payload["logprobs"])
            self.assertEqual(payload["options"]["num_predict"], judge.DEFAULT_NUM_PREDICT)
            self.assertEqual(payload["options"]["temperature"], 0)
            self.assertIs(payload["think"], False)
            self.assertFalse(payload["stream"])

    def test_other_mass_is_reported(self):
        result = judge.evaluate("x", {"route": dict(ROUTE, other="Neither")},
                                request=lambda p: _response({"C": 0.6, "A": 0.4}))
        self.assertEqual(result["answers"]["route"]["choice"], "other")
        self.assertAlmostEqual(result["answers"]["route"]["other"], 0.6, places=3)

    def test_without_logprobs_a_single_call_reads_the_text_and_says_so(self):
        """古い ollama。確率を捏造せず method=text / coverage=0 で返す。"""
        result = judge.evaluate("x", {"route": ROUTE},
                                request=lambda p: {"message": {"content": " B.\n"}})
        answer = result["answers"]["route"]
        self.assertEqual((answer["choice"], answer["method"], answer["coverage"]),
                         ("support", "text", 0.0))

    def test_without_logprobs_samples_become_a_vote(self):
        picks = iter(["A", "A", "B", "A"])
        seen = []

        def request(payload):
            seen.append(payload)
            if payload.get("logprobs"):
                return {"message": {"content": "A"}}
            return {"message": {"content": json.dumps({"answer": next(picks)})}}

        result = judge.evaluate("x", {"route": ROUTE}, samples=4, request=request)
        answer = result["answers"]["route"]
        self.assertEqual(answer["method"], "vote")
        self.assertAlmostEqual(answer["probabilities"]["billing"], 0.75, places=6)
        # 票は structured outputs（enum）で引く。1 回目の読み出し + 4 票。
        self.assertEqual(len(seen), 5)
        self.assertEqual(seen[1]["format"]["properties"]["answer"]["enum"], ["A", "B"])

    def test_an_unreadable_answer_is_an_error_not_a_guess(self):
        with self.assertRaises(judge.JudgeError):
            judge.evaluate("x", {"route": ROUTE},
                           request=lambda p: {"message": {"content": "I think billing"}})

    def test_bad_questions_fail_before_any_request(self):
        with self.assertRaises(judge.JudgeError):
            judge.evaluate("x", {"route": {"type": "choice"}},
                           request=lambda p: self.fail("request must not happen"))

    def test_abstain_uses_confidence(self):
        answers = {"a": {"confidence": 0.9}, "b": {"confidence": 0.55}}
        self.assertEqual(judge.abstained(answers, 0.7), ["b"])


if __name__ == "__main__":
    unittest.main()
