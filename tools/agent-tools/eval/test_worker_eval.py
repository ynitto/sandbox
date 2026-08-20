#!/usr/bin/env python3
"""多段セルの手順制御（run_steps）の単体テスト。LLM もエージェント CLI も呼ばない。

測りたいのは「ゲートが落ちたら**機械の不一致だけ**を足して同じ手順をやり直し、
通ったら次へ進む」こと。ここが崩れると T1seq / T1gate の比較が別物を測る。
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import worker_eval as w  # noqa: E402


class RunStepsTest(unittest.TestCase):
    def setUp(self):
        self.goals = []

    def _stub(self, results):
        """invoke の差し替え。呼ばれた goal を順に記録する。"""
        def fake(step, wt):
            self.goals.append(step["goal"])
            return 0, "out", "", 1.0
        self.addCleanup(mock.patch.object(w, "invoke", fake).stop)
        mock.patch.object(w, "invoke", fake).start()
        return results

    def test_single_goal_task_runs_once(self):
        self._stub(None)
        trace, out, _ = w.run_steps({"goal": "G"}, pathlib.Path("."))
        self.assertEqual(len(trace), 1)
        self.assertEqual(self.goals, ["G"])
        self.assertEqual(out, "out")

    def test_gate_pass_does_not_retry(self):
        self._stub(None)
        step = {"goal": "G", "gate": lambda wt: (True, "ok"), "max_retries": 2}
        trace, _, _ = w.run_steps({"steps": [step]}, pathlib.Path("."))
        self.assertEqual(len(trace), 1)
        self.assertTrue(trace[0]["gate"])

    def test_gate_fail_retries_with_machine_feedback_only(self):
        self._stub(None)
        step = {"goal": "G", "gate": lambda wt: (False, "BAD [(1, 'x', 'y')]"),
                "max_retries": 2}
        trace, _, _ = w.run_steps({"steps": [step]}, pathlib.Path("."))
        self.assertEqual(len(trace), 3)              # 初回 + 再試行 2
        self.assertEqual(self.goals[0], "G")
        # 再試行の課題文は「元の課題文 + 機械の不一致」だけ。積み増さない。
        self.assertTrue(self.goals[1].startswith("G"))
        self.assertIn("BAD [(1, 'x', 'y')]", self.goals[1])
        self.assertEqual(self.goals[1], self.goals[2])

    def test_steps_run_in_order_and_stop_retrying_once_gate_passes(self):
        seen = iter([False, True])
        self._stub(None)
        steps = [{"goal": "S1", "gate": lambda wt: (next(seen), "nope"), "max_retries": 3},
                 {"goal": "S2"}]
        trace, _, _ = w.run_steps({"steps": steps}, pathlib.Path("."))
        self.assertEqual(self.goals[0], "S1")
        self.assertTrue(self.goals[1].startswith("S1"))
        self.assertEqual(self.goals[2], "S2")
        self.assertEqual([s["step"] for s in trace], [1, 1, 2])


class AiderPolicyArgvTest(unittest.TestCase):
    def setUp(self):
        self.old = (w.MODEL, w.AGENT_POLICY, w.NUM_CTX, w.NUM_PREDICT, w.SAMPLING)
        w.MODEL = "gemma4:e4b"
        w.NUM_CTX = 0
        w.NUM_PREDICT = 0
        w.SAMPLING = {}
        self.addCleanup(self.restore)

    def restore(self):
        w.MODEL, w.AGENT_POLICY, w.NUM_CTX, w.NUM_PREDICT, w.SAMPLING = self.old

    def test_off_arm_removes_shipped_policy(self):
        w.AGENT_POLICY = "off"
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertNotIn("--agent-policy", argv)

    def test_v1_arm_keeps_exactly_one_policy(self):
        w.AGENT_POLICY = "gemma4-e4b-reliability-v1"
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertEqual(argv.count("--agent-policy"), 1)
        index = argv.index("--agent-policy")
        self.assertEqual(argv[index + 1], w.AGENT_POLICY)

    def test_num_predict_uses_adapter_option(self):
        w.AGENT_POLICY = "gemma4-e4b-reliability-v1"
        w.NUM_PREDICT = 2048
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertIn("--agent-num-predict", argv)
        self.assertNotIn("--model-settings-file", argv)


if __name__ == "__main__":
    unittest.main()
