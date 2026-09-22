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
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

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

    # OpenAI 互換の応答は `logprobs` が dict（`{"content": [...]}`）で、位置ごとのリストを
    # 読む実装には読めない。**キーはあるが読めない**——縮退表（設計 §4）の 4 行目。
    UNREADABLE = {"message": {"content": " B.\n"},
                  "logprobs": {"content": [{"token": "B", "logprob": 0.0}]}}

    def test_unreadable_logprobs_do_not_become_confidence_one(self):
        """読めない形でも確度を捏造しない。読めた事実（choice / probabilities）は残す。"""
        result = judge.evaluate("x", {"route": ROUTE}, request=lambda p: dict(self.UNREADABLE))
        answer = result["answers"]["route"]
        self.assertEqual((answer["choice"], answer["method"]), ("support", "text"))
        self.assertEqual(answer["confidence"], 0.0)
        self.assertEqual(answer["probabilities"]["support"], 1.0)

    def test_a_text_answer_abstains_even_at_a_zero_threshold(self):
        """しきい値 0.0（実測前の置き値）の呼び出しでも、確度の無い答えは素通りしない。"""
        result = judge.evaluate("x", {"route": ROUTE}, request=lambda p: dict(self.UNREADABLE))
        self.assertEqual(judge.abstained(result["answers"], 0.0), ["route"])
        self.assertEqual(judge.abstained(result["answers"], 0.0, allow_text=True), [])

    def test_unreadable_logprobs_still_reach_the_vote(self):
        """`--samples` は「logprobs が無い」ではなく「分布を読めなかった」で効く。"""
        seen = []

        def request(payload):
            seen.append(payload)
            if payload.get("logprobs"):
                return dict(self.UNREADABLE)
            return {"message": {"content": json.dumps({"answer": "A"})}}

        result = judge.evaluate("x", {"route": ROUTE}, samples=2, request=request)
        self.assertEqual(result["answers"]["route"]["method"], "vote")
        self.assertGreaterEqual(len(seen), 2, "読み出し 1 回 + 票 2 回")

    def test_abstain_uses_confidence(self):
        answers = {"a": {"confidence": 0.9}, "b": {"confidence": 0.55}}
        self.assertEqual(judge.abstained(answers, 0.7), ["b"])


class ModelSelectionTests(unittest.TestCase):
    """どの実行で judge を使うか（設定ファイル `~/.agents/agent-herd.yaml` の `judge.model`）。

    既定（auto）はローカル定義（`relative_cost` 0）だけ。モデルを指名するとクラウド CLI の
    定義でも judge を使い（判定をクラウドのトークンで払わない）、`off` ならどの定義でも
    使わない。
    """

    LOCAL = {"name": "aider", "relative_cost": 0, "default_model": "gemma4:e4b"}
    CLOUD = {"name": "claude", "relative_cost": 1}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-herd-config-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, text: str, name: str = "agent-herd.yaml"):
        pathlib.Path(self._tmp.name, name).write_text(text, encoding="utf-8")

    def test_default_is_local_definitions_only(self):
        self.assertEqual(judge.setting()["mode"], "auto")
        self.assertEqual(judge.model_for_spec(self.LOCAL), "gemma4:e4b")
        self.assertEqual(judge.model_for_spec(self.LOCAL, "gemma4:12b"), "gemma4:12b")
        self.assertIsNone(judge.model_for_spec(self.CLOUD))
        self.assertIsNone(judge.model_for_spec(None))

    def test_a_pinned_model_serves_cloud_definitions_too(self):
        self._write("judge:\n  model: gemma4:e4b\n")
        self.assertEqual(judge.model_for_spec(self.CLOUD), "gemma4:e4b")
        self.assertEqual(judge.model_for_spec(self.CLOUD, "claude-opus-5"), "gemma4:e4b",
                         "実行のモデルは持ち越さない——判定は指名したモデルに固定する")
        self.assertEqual(judge.model_for_spec(self.LOCAL, "gemma4:12b"), "gemma4:e4b")
        self.assertEqual(judge.pinned_model(), "gemma4:e4b")

    def test_off_disables_judge_for_every_definition(self):
        for text in ("judge:\n  model: off\n", "judge:\n  model: OFF\n"):
            self._write(text)
            self.assertIsNone(judge.model_for_spec(self.LOCAL), text)
            self.assertIsNone(judge.model_for_spec(self.CLOUD), text)
            self.assertIsNone(judge.pinned_model(), text)
            self.assertTrue(judge.disabled(), text)

    def test_blank_and_auto_mean_unset(self):
        for text in ("judge:\n  model: ''\n", "judge:\n  model: auto\n", "judge: {}\n", ""):
            self._write(text)
            self.assertEqual(judge.setting()["mode"], "auto", repr(text))
            self.assertEqual(judge.model_for_spec(self.LOCAL), "gemma4:e4b", repr(text))

    def test_json_file_is_read_too(self):
        self._write('{"judge": {"model": "gemma4:12b"}}', name="agent-herd.json")
        self.assertEqual(judge.model_for_spec(self.CLOUD), "gemma4:12b")

    def test_a_broken_file_falls_back_to_auto_and_says_why(self):
        self._write("judge: [\n")
        current = judge.setting()
        self.assertEqual(current["mode"], "auto")
        self.assertIn("YAML", current["error"])
        self.assertIsNone(judge.model_for_spec(self.CLOUD))

    def test_local_model_does_not_resolve_a_definition_when_pinned(self):
        self._write("judge:\n  model: gemma4:e4b\n")
        self.assertEqual(judge.local_model("no-such-definition"), "gemma4:e4b")
        self._write("judge:\n  model: off\n")
        self.assertIsNone(judge.local_model("aider"))
        self._write("")
        self.assertIsNone(judge.local_model("no-such-definition"),
                          "指名が無ければ、解決できない定義では judge を使わない")



class CalibrationPolicyTests(unittest.TestCase):
    POLICY = {"model": "gemma4:e4b", "method": "logprobs", "min_coverage": .8,
              "thresholds": {"route": .8, "filter": .6, "assess": None, "transition": None}}

    def gate(self, *, policy=POLICY, model="gemma4:e4b", purpose="route",
             confidence=.8, coverage=.8, method="logprobs", minimum=0):
        answers = {"q": {"confidence": confidence, "coverage": coverage, "method": method}}
        with mock.patch.object(judge.herdconfig, "calibration_setting", return_value=policy):
            return judge.calibrated_abstained(answers, minimum, purpose=purpose, model=model)

    def test_boundary_and_caller_minimum(self):
        self.assertEqual(self.gate(), [])
        self.assertEqual(self.gate(confidence=.7999), ["q"])
        self.assertEqual(self.gate(coverage=.7999), ["q"])
        self.assertEqual(self.gate(minimum=.9), ["q"])
        self.assertEqual(self.gate(purpose="filter", confidence=.6), [])

    def test_unmeasured_purpose_model_method_are_held(self):
        for purpose in ("assess", "transition", "quality"):
            self.assertEqual(self.gate(purpose=purpose, confidence=1), ["q"])
        self.assertEqual(self.gate(model="gemma4:12b"), ["q"])
        self.assertEqual(self.gate(method="vote"), ["q"])
        self.assertEqual(self.gate(method="text", confidence=1), ["q"])
        self.assertEqual(self.gate(coverage=float("nan")), ["q"])

    def test_no_policy_preserves_old_behavior(self):
        self.assertEqual(self.gate(policy=None, confidence=.5), [])
        self.assertEqual(self.gate(policy=None, method="text"), ["q"])

    def test_invalid_policy_holds_every_answer(self):
        with mock.patch.object(judge.herdconfig, "calibration_setting",
                               side_effect=judge.herdconfig.ConfigError("bad policy")):
            self.assertEqual(judge.calibrated_abstained({"q": {}}, 0,
                                                       purpose="route", model="gemma4:e4b"), ["q"])


if __name__ == "__main__":
    unittest.main()


class RotationTests(unittest.TestCase):
    """回転平均（ordering averaging）: 選択肢の並びを巡回させて読み、宣言順に戻して対数平均する。

    位置だけを好む応答（A に置かれたものを常に選ぶ）は打ち消され、内容を好む応答（support を
    どの位置でも選ぶ）は残る。この差が `agreement` に出る。
    """

    def test_orderings_by_type(self):
        route = judge.normalize_question("route", ROUTE)               # choice 2 択
        self.assertEqual(judge.orderings(route, 3), [[0, 1], [1, 0]], "選択肢の数まで")
        self.assertEqual(judge.orderings(route, 1), [[0, 1]])
        three = judge.normalize_question("t", dict(ROUTE, criteria={"a": "", "b": "", "c": ""}))
        self.assertEqual(judge.orderings(three, 3), [[0, 1, 2], [1, 2, 0], [2, 0, 1]])
        self.assertEqual(judge.orderings(three, 2), [[0, 1, 2], [1, 2, 0]])
        sev = judge.normalize_question("sev", SEVERITY)                # score は正順と逆順だけ
        self.assertEqual(judge.orderings(sev, 3), [[0, 1, 2], [2, 1, 0]])
        self.assertEqual(judge.orderings(judge.normalize_question("u", URGENT), 5), [[0, 1], [1, 0]])

    def test_rotated_prompt_moves_options_and_keeps_labels_in_place(self):
        q = judge.normalize_question("route", ROUTE)
        prompt = judge.build_prompt("s", q, [1, 0])
        self.assertIn("A. support: Other requests", prompt)
        self.assertIn("B. billing: Billing and refunds", prompt)
        self.assertLess(prompt.index("A. support"), prompt.index("B. billing"))

    def test_position_bias_is_averaged_out(self):
        """A を常に好む応答——回転すると打ち消されて五分、agreement は 0.5。"""
        result = judge.evaluate("x", {"route": ROUTE}, rotations=2,
                                request=lambda p: _response({"A": 0.9, "B": 0.1}))
        answer = result["answers"]["route"]
        self.assertEqual(answer["rotations"], 2)
        self.assertAlmostEqual(answer["probabilities"]["billing"], 0.5, places=3)
        self.assertAlmostEqual(answer["confidence"], 0.5, places=3)
        self.assertEqual(answer["agreement"], 0.5)
        self.assertEqual(result["usage"], {"tokens_in": 20, "tokens_out": 2}, "2 回分の消費")

    def test_content_preference_survives_rotation(self):
        """support を好む応答は、どの位置でも support——agreement 1.0、確率はそのまま。"""
        def request(payload):
            prompt = payload["messages"][0]["content"]
            label = "A" if "A. support" in prompt else "B"
            return _response({label: 0.8, ("B" if label == "A" else "A"): 0.2})

        result = judge.evaluate("x", {"route": ROUTE}, rotations=2, request=request)
        answer = result["answers"]["route"]
        self.assertEqual(answer["choice"], "support")
        self.assertAlmostEqual(answer["probabilities"]["support"], 0.8, places=3)
        self.assertEqual(answer["agreement"], 1.0)

    def test_score_reversal_maps_back_to_the_scale(self):
        """逆順で C に立った low の質量は、宣言順の low に戻る。"""
        def request(payload):
            prompt = payload["messages"][0]["content"]
            return _response({"A": 0.7, "B": 0.2, "C": 0.1} if "A. low" in prompt
                             else {"C": 0.7, "B": 0.2, "A": 0.1})

        answer = judge.evaluate("x", {"sev": SEVERITY}, rotations=3, request=request)["answers"]["sev"]
        self.assertEqual(answer["rotations"], 2)
        self.assertEqual(answer["bucket"], "low")
        self.assertAlmostEqual(answer["probabilities"]["low"], 0.7, places=3)
        self.assertAlmostEqual(answer["score"], 0.4, places=3)    # 0.7*0 + 0.2*1 + 0.1*2

    def test_one_rotation_is_a_single_read(self):
        sent = []
        answer = judge.evaluate("x", {"route": ROUTE}, rotations=1,
                                request=lambda p: sent.append(p) or _response({"A": 0.7, "B": 0.3}))
        self.assertEqual(len(sent), 1)
        self.assertEqual((answer["answers"]["route"]["rotations"], answer["answers"]["route"]["agreement"]),
                         (1, 1.0))

    def test_unreadable_first_read_falls_back_without_rotating(self):
        """縮退（票・本文）は 1 回読みのまま——回転は分布を読めた問いだけ。"""
        sent = []

        def request(payload):
            sent.append(payload)
            return {"message": {"content": " B.\n"}}

        result = judge.evaluate("x", {"route": ROUTE}, rotations=3, request=request)
        self.assertEqual(result["answers"]["route"]["method"], "text")
        self.assertEqual(len(sent), 1)

    def test_a_rotation_that_cannot_be_read_is_skipped(self):
        replies = iter([_response({"A": 0.9, "B": 0.1}), {"message": {"content": "?"}}])
        answer = judge.evaluate("x", {"route": ROUTE}, rotations=2,
                                request=lambda p: next(replies))["answers"]["route"]
        self.assertEqual(answer["rotations"], 1)
        self.assertAlmostEqual(answer["probabilities"]["billing"], 0.9, places=3)

    def test_average_orderings_is_a_log_mean(self):
        probs, agreement = judge.average_orderings([[0.9, 0.1], [0.1, 0.9]])
        self.assertAlmostEqual(probs[0], 0.5, places=6)
        self.assertEqual(agreement, 0.5)
        probs, agreement = judge.average_orderings([[0.8, 0.2], [0.6, 0.4]])
        self.assertGreater(probs[0], probs[1])
        self.assertEqual(agreement, 1.0)

    def test_default_rotations_come_from_config(self):
        with tempfile.TemporaryDirectory(prefix="agent-herd-config-") as tmp, \
                mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": tmp}):
            self.assertEqual(judge.default_rotations(), judge.DEFAULT_ROTATIONS)
            pathlib.Path(tmp, "agent-herd.yaml").write_text("judge:\n  rotations: 2\n", encoding="utf-8")
            self.assertEqual(judge.default_rotations(), 2)
            sent = []
            judge.evaluate("x", {"route": ROUTE},
                           request=lambda p: sent.append(p) or _response({"A": 0.7, "B": 0.3}))
            self.assertEqual(len(sent), 2)
            pathlib.Path(tmp, "agent-herd.yaml").write_text("judge:\n  rotations: many\n", encoding="utf-8")
            self.assertEqual(judge.default_rotations(), judge.DEFAULT_ROTATIONS, "壊れた設定は既定へ")


class KeepAliveTests(unittest.TestCase):
    """判定のモデルを ollama に残す時間。環境変数が優先で、無ければ設定を見る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-herd-config-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"AGENT_PROJECT_AGENTS_HOME": self._tmp.name,
                                               "AGENT_OLLAMA_KEEP_ALIVE": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sent(self):
        sent = []
        judge.evaluate("x", {"route": ROUTE},
                       request=lambda p: sent.append(p) or _response({"A": 0.7, "B": 0.3}))
        return sent[0]

    def test_absent_config_sends_nothing(self):
        self.assertIsNone(judge.default_keep_alive())
        self.assertNotIn("keep_alive", self._sent())

    def test_config_reaches_the_payload(self):
        pathlib.Path(self._tmp.name, "agent-herd.yaml").write_text(
            "judge:\n  keep_alive: 30m\n", encoding="utf-8")
        self.assertEqual(judge.default_keep_alive(), "30m")
        self.assertEqual(self._sent()["keep_alive"], "30m")

    def test_environment_wins_over_config(self):
        pathlib.Path(self._tmp.name, "agent-herd.yaml").write_text(
            "judge:\n  keep_alive: 30m\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"AGENT_OLLAMA_KEEP_ALIVE": "1h"}):
            self.assertEqual(self._sent()["keep_alive"], "1h")

    def test_broken_config_does_not_stop_the_judgement(self):
        pathlib.Path(self._tmp.name, "agent-herd.yaml").write_text(
            "judge:\n  keep_alive: forever\n", encoding="utf-8")
        self.assertIsNone(judge.default_keep_alive())
        self.assertNotIn("keep_alive", self._sent())

