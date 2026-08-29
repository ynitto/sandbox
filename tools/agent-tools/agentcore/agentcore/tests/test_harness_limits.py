#!/usr/bin/env python3
"""回数上限の設定化と、停止理由の共通語彙（計画 2026-08-29 A1 / レビュー P1）。

判定そのものは各層の既存実装が持つ。ここが縛るのは 2 つだけ:

- 上限の**決まり方**（宣言 ＞ 環境変数 ＞ 層の既定）。既定は 8 のまま動かない。
- 停止理由が**層をまたいで同じ綴りで**返ること。名乗らないまま止まる経路を作らない。
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from agentcore import limits, ollama_adapter, ollama_loop, stopreason  # noqa: E402
from agentcore.harness import statemachine as sm  # noqa: E402
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402


class MaxRoundsResolutionTest(unittest.TestCase):
    """上限の決まり方。環境変数は測定の条件、宣言は運用の意思——宣言が勝つ。"""

    def setUp(self):
        for name in (tl._TL_MAX_ROUNDS_ENV, tl._TL_MAX_ROUNDS_WRITE_ENV):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def test_default_is_unchanged(self):
        self.assertEqual(tl._tl_max_rounds(), tl._TL_MAX_TOOL_ROUNDS)

    def test_declaration_wins_over_environment(self):
        os.environ[tl._TL_MAX_ROUNDS_ENV] = "2"
        os.environ[tl._TL_MAX_ROUNDS_WRITE_ENV] = "3"
        self.assertEqual(tl._tl_max_rounds(6, write=True), 6)

    def test_write_environment_applies_only_to_write_states(self):
        os.environ[tl._TL_MAX_ROUNDS_WRITE_ENV] = "3"
        self.assertEqual(tl._tl_max_rounds(write=True), 3)
        # 編集を宣言していないステートは締めない（調査の周を殺さない）。
        self.assertEqual(tl._tl_max_rounds(write=False), tl._TL_MAX_TOOL_ROUNDS)

    def test_global_environment_applies_to_every_state(self):
        os.environ[tl._TL_MAX_ROUNDS_ENV] = "4"
        self.assertEqual(tl._tl_max_rounds(write=False), 4)
        self.assertEqual(tl._tl_max_rounds(write=True), 4)

    def test_unreadable_values_fall_through(self):
        os.environ[tl._TL_MAX_ROUNDS_ENV] = "zero"
        # 壊れた宣言・0 以下は「宣言なし」と同じ扱い。黙って 0 周にはしない。
        self.assertEqual(tl._tl_max_rounds(0), tl._TL_MAX_TOOL_ROUNDS)
        self.assertEqual(tl._tl_max_rounds("-1"), tl._TL_MAX_TOOL_ROUNDS)

    def test_caller_default_is_used_when_nothing_declared(self):
        self.assertEqual(tl._tl_max_rounds(default=5), 5)


class StateDeclaredRoundsTest(unittest.TestCase):
    """ステートの `max_tool_rounds` が実際にループの周回数になること。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-sm-rounds-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        machine = pathlib.Path(self.repo, ".statemachine", "one-step")
        (machine / "actions").mkdir(parents=True)
        (machine / "workflow.yaml").write_text("states: {make: {}}\n", encoding="utf-8")
        (machine / "actions" / "make.md").write_text("OK を返す。\n", encoding="utf-8")
        self.workflow = str(machine / "workflow.yaml")
        for name in (tl._TL_MAX_ROUNDS_ENV, tl._TL_MAX_ROUNDS_WRITE_ENV):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def _rounds_used(self, *, declared=None, state=None):
        """契約を満たさない応答を返し続け、モデルが何回呼ばれたかを数える。"""
        with patch_harness("_tl_run_agent", return_value="まだ考え中") as agent:
            with self.assertRaises(sm.StateMachineHarnessError):
                sm._sm_execute_action(
                    workflow_path=self.workflow, state_id="make",
                    state={"action_file": "actions/make.md",
                           "output_validator": "startswith:OK", **(state or {})},
                    context={}, cwd=self.repo, agent={},
                    log_file=os.path.join(self.repo, "run.jsonl"), touched=set(),
                    max_tool_rounds=declared)
        return agent.call_count

    def test_declared_rounds_bound_the_loop(self):
        self.assertEqual(self._rounds_used(declared=2), 2)

    def test_write_environment_binds_states_that_declare_write(self):
        os.environ[tl._TL_MAX_ROUNDS_WRITE_ENV] = "3"
        self.assertEqual(self._rounds_used(state={"write": "out.txt"}), 3)
        # `write:` を宣言していないステートは層の既定のまま。
        self.assertEqual(self._rounds_used(), tl._TL_MAX_TOOL_ROUNDS)


class StateCheckSpecTest(unittest.TestCase):
    """宣言の受け渡し。古い statemachine-use（キーを返さない版）でも落ちないこと。"""

    def _spec(self, payload):
        with mock.patch.object(sm, "_sm_harness_script",
                               return_value=json.dumps(payload)):
            return sm._sm_state_check_spec(
                scripts={"next": "next_state.py"}, workflow_path="w.yaml",
                state_id="make", cwd=".", log_file="run.jsonl")

    def test_declared_value_is_passed_through(self):
        self.assertEqual(self._spec({"state": "make", "max_tool_rounds": 3})
                         ["max_tool_rounds"], 3)

    def test_missing_key_means_no_declaration(self):
        # 配布済みの古いスキルは このキーを返さない。None ＝ 宣言なしとして既定へ倒す。
        self.assertIsNone(self._spec({"state": "make"})["max_tool_rounds"])


class StopReasonVocabularyTest(unittest.TestCase):
    def test_layer_spellings_normalize_to_the_shared_vocabulary(self):
        # ollama_loop の既存 status は改名しない（replay の契約）。読むときに寄せる。
        self.assertEqual(stopreason.normalize("done"), stopreason.FINAL)
        self.assertEqual(stopreason.normalize("no_progress"), stopreason.NO_PROGRESS)
        self.assertEqual(stopreason.normalize("escalate"), stopreason.CHECK_EXHAUSTED)

    def test_unknown_reasons_are_not_given_a_name(self):
        self.assertEqual(stopreason.normalize("そのうち止まった"), "")
        self.assertFalse(stopreason.is_escalating(None))

    def test_completed_and_escalating_do_not_overlap(self):
        self.assertFalse(stopreason.COMPLETED & stopreason.ESCALATING)
        for name in stopreason.COMPLETED:
            self.assertFalse(stopreason.is_escalating(name))


class RunGoalStopReasonTest(unittest.TestCase):
    """層3（ハーネスがツールループを供給する経路）の停止理由。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-loop-tl-stop-")
        self.repo = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.log = os.path.join(self.repo, "run.jsonl")

    def _run(self, responses, *, acceptance=None, max_rounds=0):
        with patch_harness("_tl_run_control",
                           side_effect=lambda *_a, **_k: next(responses)), \
                patch_harness("_tl_run_agent", return_value="書きました"):
            return tl.run_goal(goal="やる", cwd=self.repo, agent={}, log_file=self.log,
                               acceptance=list(acceptance or []), max_rounds=max_rounds)

    def test_final_is_reported(self):
        responses = iter(['{"type":"final","output":"OK"}'])
        self.assertEqual(self._run(responses)["stopReason"], stopreason.FINAL)

    def test_round_limit_is_reported(self):
        # final を返さないまま上限へ到達。偽の完了にせず、名乗って止まる。
        responses = iter(['{"type":"read_files","paths":["nope.txt"]}'] * 4)
        result = self._run(responses, max_rounds=2)
        self.assertEqual(result["stopReason"], stopreason.MAX_ROUNDS)
        self.assertTrue(stopreason.is_escalating(result["stopReason"]))

    def test_machine_acceptance_reports_verified(self):
        # 受入条件を満たした時点で final を待たずに終える既存経路（C5）。
        target = pathlib.Path(self.repo, "out.txt")
        writes = iter(range(1, 9))

        def write(*_a, **_k):
            target.write_text(f"書いた {next(writes)}\n", encoding="utf-8")
            return "書きました"

        with patch_harness("_tl_run_control",
                           return_value='{"type":"write_files","paths":["out.txt"]}'), \
                patch_harness("_tl_run_agent", side_effect=write):
            result = tl.run_goal(goal="やる", cwd=self.repo, agent={}, log_file=self.log,
                                 acceptance=["`out.txt` が更新されている"], max_rounds=3)
        self.assertEqual(result["stopReason"], stopreason.VERIFIED)


if __name__ == "__main__":
    unittest.main()


class FailingTestSelectionTest(unittest.TestCase):
    """検査が落ちた後の再投入で戻す材料（レビュー P2 / 制限付き実行案 §7）。"""

    PYTEST_OUTPUT = """\
============================= test session starts ==============================
collected 41 items

tests/test_billing.py ....F...............                              [ 48%]
tests/test_report.py ...................                                [100%]

=================================== FAILURES ===================================
_____________________________ test_prorate_rounds ______________________________

    def test_prorate_rounds():
>       assert prorate(100, 3) == 34
E       assert 33 == 34
E        +  where 33 = prorate(100, 3)

tests/test_billing.py:18: AssertionError
=========================== short test summary info ============================
FAILED tests/test_billing.py::test_prorate_rounds - assert 33 == 34
1 failed, 40 passed in 1.20s
"""

    def _note(self, detail, *, feedback=True):
        result = {"context": {"check_status": "1"}, "argv": ["pytest", "-q"],
                  "error": "", "stderr": "", "stdout": detail}
        return sm._sm_check_note(result, 1, 3, feedback=feedback)

    def test_only_the_failing_lines_are_sent_back(self):
        note = self._note(self.PYTEST_OUTPUT)

        self.assertIn("FAILED tests/test_billing.py::test_prorate_rounds", note)
        self.assertIn("assert 33 == 34", note)
        # 通ったテストの進捗行は材料ではない（ここを削るのが選別の目的）。
        self.assertNotIn("test session starts", note)
        self.assertNotIn("40 passed", note)

    def test_unparseable_output_falls_back_and_says_so(self):
        # 選別できない出力は従来どおり末尾を渡す。**省略した事実を書く**。
        detail = "x" * (sm._SM_CHECK_OUTPUT_LIMIT + 500)
        note = self._note(detail)

        self.assertIn("the earlier output was omitted", note)
        self.assertIn("x" * 50, note)

    def test_short_unparseable_output_is_sent_whole(self):
        note = self._note("ちょっと落ちた")

        self.assertIn("Check output:\nちょっと落ちた", note)
        self.assertNotIn("omitted", note)

    def test_feedback_off_still_sends_no_output(self):
        note = self._note(self.PYTEST_OUTPUT, feedback=False)

        self.assertNotIn("FAILED", note)

    def test_matches_the_skill_implementation(self):
        # 2 実装が同じ行を選ぶことを固定する（正典は statemachine-use の engine）。
        skill = sm._sm_resolve_skill("statemachine-use", os.getcwd())
        self.assertIsNotNone(skill, "statemachine-use スキルの実体が必要")
        sys.path.insert(0, skill["root"])
        self.addCleanup(lambda: sys.path.remove(skill["root"]))
        from scripts.engine import failing_lines  # noqa: E402

        for detail in (self.PYTEST_OUTPUT, "ちょっと落ちた", "",
                       "ERROR tests/test_x.py - ImportError: cannot import name 'f'"):
            self.assertEqual(sm._sm_failing_lines(detail), failing_lines(detail), detail[:40])


class OllamaLoopRoundsTest(unittest.TestCase):
    """`agent-ollama` 経路の上限も同じ 1 実装で決まること（A1 の統一）。

    ここが繋がっていないと、同じ「回数上限」が層ごとに別の決まり方を持つ——
    A1 が潰したかったのはまさにそれである。
    """

    def setUp(self):
        for name in (limits.MAX_ROUNDS_ENV, limits.MAX_ROUNDS_WRITE_ENV):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def _rounds(self, tokens):
        return ollama_adapter.parse_args(tokens)["max_rounds"]

    def test_default_is_unchanged(self):
        self.assertEqual(self._rounds(["m"]), ollama_loop.DEFAULT_MAX_ROUNDS)

    def test_declared_flag_wins_over_environment(self):
        # 宣言は CLI 定義の write_args に載る。測定条件が定義の予算を黙って上書きしない。
        os.environ[limits.MAX_ROUNDS_ENV] = "3"
        self.assertEqual(self._rounds(["m", "--max-rounds", "12"]), 12)

    def test_write_environment_binds_the_bash_toolset(self):
        os.environ[limits.MAX_ROUNDS_WRITE_ENV] = "3"
        self.assertEqual(self._rounds(["m", "--tools", "bash"]), 3)
        # read セットは作業ツリーを変えられないので、編集用の腕では締めない。
        self.assertEqual(self._rounds(["m", "--tools", "read"]),
                         ollama_loop.DEFAULT_MAX_ROUNDS)

    def test_global_environment_binds_every_toolset(self):
        os.environ[limits.MAX_ROUNDS_ENV] = "4"
        self.assertEqual(self._rounds(["m", "--tools", "read"]), 4)
        self.assertEqual(self._rounds(["m", "--tools", "bash"]), 4)

    def test_write_capability_matches_the_toolset_gate(self):
        # 「編集の周か」の判定は check_command のゲートと同じ根拠で決める
        # （read セットで書けないことは既存テストが縛っている）。
        self.assertTrue(ollama_loop.toolset_writes("bash"))
        self.assertTrue(ollama_loop.toolset_writes(None))     # 既定は bash
        self.assertFalse(ollama_loop.toolset_writes("read"))
