"""ペイン経路の受入条件・証跡ゲート（設計 2026-08-27 §7.3 B / 実装計画 段 9）。

以前ペインは**画面が idle に戻っただけで完了**として返していた。entry が
`acceptance` を宣言していても誰も見ておらず、宣言したつもりの検証が効かない
——ヘッドレスには同じ判定が既にあったので、経路によって done の意味が違っていた。

ここが見るのは 4 つ。
①dispatch の前に指紋を取ること、②ターン完了時に照合して落ちること、
③`verifiedBy` がヘッドレスと同じ語彙で出ること、
④判定は 1 実装（`toolloop.acceptance_outcome`）を層2 と共有すること。

tmux もエージェント CLI も起こさない。ファイルの中身と宣言だけで決まる判定である。
"""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402

from agentcore.harness import toolloop  # noqa: E402


def _session_manager():
    mgr = al.SessionManager.__new__(al.SessionManager)
    mgr._panes = {"e1": "%1"}
    mgr._prompt_names = {"e1": "gate"}
    mgr._tmux_names = {}
    mgr._prompt_cwds = {"e1": "/tmp"}
    mgr._owners = {"e1": "scheduled"}
    mgr._ownership = {"e1": "managed-persistent"}
    mgr._generation = {"e1": 1}
    mgr._effective_model = {"e1": None}
    mgr._launch_fingerprint = {}
    mgr._instr_rev = {}
    mgr._restart_locks = {}
    mgr._state_extras = {}
    mgr._lock = threading.Lock()
    mgr._target_path = "/tmp"
    mgr._startup_timeout = 5
    mgr.send_prompt = mock.Mock(return_value=True)
    mgr.get_generation = mock.Mock(return_value=1)
    return mgr


class PaneAcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="al-gate-")
        self.addCleanup(lambda: None)
        self.report = Path(self.dir) / "report.md"
        self.criteria = ["`report.md` が更新されている"]
        self.entry = {"id": "e1", "name": "gate", "prompt": "書いて",
                      "interval_minutes": 60, "enabled": True,
                      "cwd": self.dir, "acceptance": self.criteria}
        mgr = _session_manager()
        self.sched = al.PeriodicScheduler(mgr, [dict(self.entry)], semaphore=None,
                                          slot_monitor=None, workspace=self.dir)
        self.sched._fail_execution = mock.Mock()
        self.req = al.make_dispatch_request(
            source="schedule", entry_id="e1", prompt="書いて", request_id="r")

    def _stamp(self):
        self.sched._stamp_acceptance(self.req, self.entry)

    def test_an_unchanged_file_does_not_pass_the_gate(self):
        """受入条件その 1。宣言したファイルが変わっていなければ done にしない。"""
        self.report.write_text("まだ何もしていない\n", encoding="utf-8")
        self._stamp()
        self.assertFalse(self.sched._acceptance_gate(self.req, "%1"))
        self.assertEqual(self.sched._fail_execution.call_args.kwargs["reason"],
                         "acceptance_failed")

    def test_a_changed_file_passes_and_names_the_verifier(self):
        """受入条件その 2。`verifiedBy` がヘッドレスと同じ語彙で出る。"""
        self.report.write_text("前\n", encoding="utf-8")
        self._stamp()
        self.report.write_text("書きました\n", encoding="utf-8")
        with self.assertLogs("agent-loop", level="INFO") as logs:
            self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        self.sched._fail_execution.assert_not_called()
        self.assertTrue(any("machine" in line for line in logs.output),
                        "機械層で検証したことが記録に残る")

    def test_a_missing_file_does_not_pass_the_gate(self):
        self._stamp()
        self.assertFalse(self.sched._acceptance_gate(self.req, "%1"))

    def test_an_entry_without_acceptance_is_untouched(self):
        """宣言が無ければゲートは何もしない（既存の entry の挙動を変えない）。"""
        self.sched._stamp_acceptance(self.req, {"id": "e1", "cwd": self.dir})
        self.assertNotIn("_acceptance", self.req.get("meta") or {})
        self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        self.sched._fail_execution.assert_not_called()

    def test_the_stamp_is_taken_before_the_prompt_is_sent(self):
        """指紋は送信より前に取る。後で取ると「この実行で変わったか」が言えない。"""
        self.report.write_text("前\n", encoding="utf-8")
        order = []
        real_send = self.sched._session_mgr.send_prompt

        def send(*a, **k):
            order.append(("send", self.req.get("meta", {}).get("_acceptance") is not None))
            return real_send(*a, **k)

        self.sched._session_mgr.send_prompt = send
        with mock.patch.object(al, "_CLI_PROFILE") as prof, \
                mock.patch.object(self.sched, "_track_active"):
            prof.rewrite_slash = lambda line: line
            prof.clear_command = ""
            self.sched._dispatch_prompt(dict(self.entry), "%1", req=self.req)
        self.assertEqual(order, [("send", True)], "送る時点で指紋が控えられている")

    def test_the_gate_is_spent_once_per_turn(self):
        """2 度目の完了通知で同じ照合を繰り返さない（指紋は 1 ターンのもの）。"""
        self.report.write_text("前\n", encoding="utf-8")
        self._stamp()
        self.report.write_text("後\n", encoding="utf-8")
        self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        self.report.write_text("前\n", encoding="utf-8")   # 元へ戻す = 未変更と同じ
        self.assertTrue(self.sched._acceptance_gate(self.req, "%1"),
                        "指紋を消費済みなので再判定しない")

    def test_the_pane_gate_also_observes_the_git_diff(self):
        """ペインでも宣言外のファイル変更が観測できる（設計 段 9b）。

        指紋が答えるのは「名指ししたパスが変わったか」だけ。**宣言外を触ったか**は
        git 差分でしか見えない——ペインにもヘッドレスにも同じく効いていた制約である。
        """
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        for key, val in (("user.email", "t@example.com"), ("user.name", "t")):
            subprocess.run(["git", "config", key, val], cwd=self.dir, check=True)
        self.report.write_text("前\n", encoding="utf-8")
        (Path(self.dir) / "other.md").write_text("触っていない\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=self.dir, check=True,
                       stdout=subprocess.DEVNULL)
        self._stamp()
        self.assertEqual(self.req["meta"]["_acceptance"]["git"], {},
                         "dispatch 前の git 状態も控える")
        self.report.write_text("後\n", encoding="utf-8")
        (Path(self.dir) / "other.md").write_text("勝手に触った\n", encoding="utf-8")
        with self.assertLogs("agent-loop", level="INFO") as logs:
            self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        line = next(l for l in logs.output if "event=acceptance_checked" in l)
        self.assertIn("files=2", line, "宣言外の other.md も数える")

    def test_outside_git_the_pane_gate_falls_back_to_fingerprints(self):
        """非 git の作業フォルダでは現行どおり（後方互換）。"""
        self.report.write_text("前\n", encoding="utf-8")
        self._stamp()
        self.assertIsNone(self.req["meta"]["_acceptance"]["git"])
        self.report.write_text("後\n", encoding="utf-8")
        (Path(self.dir) / "other.md").write_text("宣言外\n", encoding="utf-8")
        with self.assertLogs("agent-loop", level="INFO") as logs:
            self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        line = next(l for l in logs.output if "event=acceptance_checked" in l)
        self.assertIn("files=1", line, "指紋だけなので名指しした 1 枚")

    def test_the_outcome_is_recorded_in_the_dispatch_log(self):
        """人向けの 1 行だけだと後から機械で追えない（設計 §7.4-2 / 段 11）。"""
        self.report.write_text("前\n", encoding="utf-8")
        self._stamp()
        self.report.write_text("後\n", encoding="utf-8")
        with self.assertLogs("agent-loop", level="INFO") as logs:
            self.sched._acceptance_gate(self.req, "%1")
        line = next(l for l in logs.output if "event=acceptance_checked" in l)
        self.assertIn("ok=True", line)
        self.assertIn("verifiedBy=machine", line)
        self.assertIn("files=1", line)
        self.assertIn("errors=0", line)

    def test_a_broken_gate_does_not_hang_the_turn(self):
        """照合が落ちてもターンを宙吊りにしない（落ちるとスロットが返らない）。"""
        self._stamp()
        with mock.patch.object(toolloop, "acceptance_outcome",
                               side_effect=RuntimeError("boom")):
            self.assertTrue(self.sched._acceptance_gate(self.req, "%1"))
        self.sched._fail_execution.assert_not_called()


class TheGateSharesOneImplementationWithHeadlessTests(unittest.TestCase):
    """層2 とペインは同じ関数を引く。判定を 2 か所に書かない。"""

    def test_the_outcome_keys_match_the_headless_result_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.md"
            target.write_text("前\n", encoding="utf-8")
            criteria = ["`out.md` が更新されている"]
            before = toolloop.acceptance_stamps(criteria, tmp)
            target.write_text("後\n", encoding="utf-8")
            got = toolloop.acceptance_outcome(criteria, cwd=tmp, stamps_before=before)
        self.assertEqual(set(got), {"ok", "verified", "verifiedBy", "files",
                                    "evidenceErrors"})
        self.assertTrue(got["ok"])
        self.assertEqual(got["verifiedBy"], "machine")

    def test_without_an_agent_the_judge_layer_does_not_run(self):
        """報告本文を取れない経路では機械層だけ。黙って pass にはしない。"""
        with tempfile.TemporaryDirectory() as tmp:
            criteria = ["レポートに前週比が含まれている"]     # パスを含まない自然文
            got = toolloop.acceptance_outcome(criteria, cwd=tmp, stamps_before={})
        self.assertEqual(got["verifiedBy"], "", "judge が出ないので後から区別できる")
        self.assertFalse(got["verified"])
        self.assertTrue(got["ok"], "機械層に照合対象が無いのは fail ではない")


if __name__ == "__main__":
    unittest.main()
