#!/usr/bin/env python3
"""headless 実行ログを追う tmux ウィンドウ（opt-in）。"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402
# ハーネスの実装は agentcore（agent-herd と共有する 1 実装）。agent_loop は
# 委譲するだけなので、差し替えも参照もそちらへ向ける。sys.path は
# agent_loop の import が通してくれる。
from agentcore.harness import toolloop as tl  # noqa: E402


class LogWindowNameTests(unittest.TestCase):
    def test_target_syntax_characters_are_stripped(self):
        """`.` と `:` は tmux のターゲット構文に食われるので名前に残さない。"""
        self.assertEqual(al._log_window_name("issues.watch:1"), "log-issues-watch-1")

    def test_empty_label_falls_back(self):
        self.assertEqual(al._log_window_name(""), "log-run")

    def test_name_is_bounded(self):
        self.assertLessEqual(len(al._log_window_name("あ" * 100)), len("log-") + 20)


class OpenLogWindowTests(unittest.TestCase):
    def test_no_tmux_is_not_an_error(self):
        with mock.patch.object(al.shutil, "which", return_value=None):
            self.assertFalse(al.open_log_window("s", "n", "/tmp/a.jsonl"))

    def test_empty_log_file_is_refused(self):
        self.assertFalse(al.open_log_window("s", "n", ""))

    def test_creates_the_session_when_absent(self):
        calls = []

        def _tmux(*args, **kw):
            calls.append(args)
            code = 1 if args[0] == "has-session" else 0
            return types.SimpleNamespace(returncode=code, stdout="", stderr="")

        with mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al, "_tmux_cmd", _tmux):
            self.assertTrue(al.open_log_window("sess", "issues", "/tmp/a b.jsonl"))
        self.assertEqual(calls[0][0], "has-session")
        self.assertEqual(calls[1][0], "new-session")
        # パスは quote して渡す（空白入りでも 1 引数のまま）
        self.assertIn("'/tmp/a b.jsonl'", calls[1][-1])

    def test_reuses_one_window_per_entry(self):
        """実行のたびに窓を増やさない——5 分間隔なら 1 日 288 枚になる。"""
        calls = []

        def _tmux(*args, **kw):
            calls.append(args)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al, "_tmux_cmd", _tmux):
            self.assertTrue(al.open_log_window("sess", "issues", "/tmp/new.jsonl"))
        verbs = [c[0] for c in calls]
        self.assertEqual(verbs, ["has-session", "has-session", "respawn-window"])
        self.assertNotIn("new-window", verbs)

    def test_tmux_failure_is_swallowed(self):
        with mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al, "_tmux_cmd", side_effect=RuntimeError("boom")):
            self.assertFalse(al.open_log_window("sess", "issues", "/tmp/a.jsonl"))


class SchedulerHookupTests(unittest.TestCase):
    def _scheduler(self, tool_config):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._tool_config = dict(tool_config)
        s._session_mgr = types.SimpleNamespace(
            get_attach_session_name=mock.Mock(return_value="agent-loop-x"))
        return s

    def test_off_by_default(self):
        s = self._scheduler({})
        with mock.patch.object(al, "open_log_window") as opener:
            s._open_headless_log_window({"name": "n"}, "/tmp/a.jsonl")
        opener.assert_not_called()

    def test_opens_with_the_entry_name_when_enabled(self):
        s = self._scheduler({"headless_window": True})
        with mock.patch.object(al, "open_log_window", return_value=True) as opener:
            s._open_headless_log_window({"name": "issues", "id": "e1"}, "/tmp/a.jsonl")
        opener.assert_called_once_with("agent-loop-x", "issues", "/tmp/a.jsonl")

    def test_falls_back_to_the_entry_id(self):
        s = self._scheduler({"headless_window": True})
        with mock.patch.object(al, "open_log_window", return_value=True) as opener:
            s._open_headless_log_window({"id": "e1"}, "/tmp/a.jsonl")
        opener.assert_called_once_with("agent-loop-x", "e1", "/tmp/a.jsonl")

    def test_no_session_name_means_no_window(self):
        s = self._scheduler({"headless_window": True})
        s._session_mgr = types.SimpleNamespace(
            get_attach_session_name=mock.Mock(return_value=""))
        with mock.patch.object(al, "open_log_window") as opener:
            s._open_headless_log_window({"name": "n"}, "/tmp/a.jsonl")
        opener.assert_not_called()

    def test_session_manager_failure_does_not_propagate(self):
        s = self._scheduler({"headless_window": True})
        s._session_mgr = types.SimpleNamespace(
            get_attach_session_name=mock.Mock(side_effect=RuntimeError("boom")))
        with mock.patch.object(al, "open_log_window") as opener:
            s._open_headless_log_window({"name": "n"}, "/tmp/a.jsonl")
        opener.assert_not_called()


class LogViewRoutingTests(unittest.TestCase):
    """headless 実行ログの見せ場所の既定はペイン（controller と分割）。

    同時実行数 1 でも controller ペインの中に実行の様子が混ざらないようにする。
    headless_window: true は従来どおり専用ウィンドウ、headless_pane: false は何も開かない。
    """

    def _scheduler(self, tool_config):
        s = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        s._tool_config = dict(tool_config)
        s._session_mgr = types.SimpleNamespace(
            get_attach_session_name=mock.Mock(return_value="agent-loop-x"),
            open_headless_log_pane=mock.Mock(return_value=True))
        return s

    def test_default_opens_a_pane_per_entry(self):
        s = self._scheduler({})
        s._open_headless_log_view({"name": "issues", "id": "e1"}, "/tmp/a.jsonl")
        s._session_mgr.open_headless_log_pane.assert_called_once_with(
            "e1", "issues", "/tmp/a.jsonl")

    def test_headless_window_takes_precedence(self):
        s = self._scheduler({"headless_window": True})
        with mock.patch.object(al, "open_log_window", return_value=True) as opener:
            s._open_headless_log_view({"name": "issues", "id": "e1"}, "/tmp/a.jsonl")
        opener.assert_called_once()
        s._session_mgr.open_headless_log_pane.assert_not_called()

    def test_headless_pane_false_opens_nothing(self):
        s = self._scheduler({"headless_pane": False})
        with mock.patch.object(al, "open_log_window") as opener:
            s._open_headless_log_view({"name": "n", "id": "e1"}, "/tmp/a.jsonl")
        opener.assert_not_called()
        s._session_mgr.open_headless_log_pane.assert_not_called()

    def test_pane_failure_does_not_propagate(self):
        s = self._scheduler({})
        s._session_mgr.open_headless_log_pane.side_effect = RuntimeError("boom")
        s._open_headless_log_view({"name": "n", "id": "e1"}, "/tmp/a.jsonl")


class OpenHeadlessLogPaneTests(unittest.TestCase):
    def _mgr(self, layout=None):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._lock = al.threading.Lock()
        mgr._headless_log_panes = {}
        mgr._layout_window_target = layout
        mgr._target_path = "/tmp"
        return mgr

    def test_outside_tmux_opens_nothing(self):
        mgr = self._mgr(layout=None)
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al, "_tmux_cmd") as tmux:
            self.assertFalse(mgr.open_headless_log_pane("e1", "n", "/tmp/a.jsonl"))
        tmux.assert_not_called()

    def test_creates_then_reuses_one_pane_per_entry(self):
        mgr = self._mgr(layout="sess:0")
        create = mock.Mock(return_value="%7")
        mgr._create_worker_pane = create
        mgr._pane_exists = mock.Mock(return_value=True)
        ok_cmd = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1"}), \
             mock.patch.object(al.shutil, "which", return_value="/usr/bin/tmux"), \
             mock.patch.object(al, "_tmux_cmd", return_value=ok_cmd) as tmux:
            self.assertTrue(mgr.open_headless_log_pane("e1", "issues", "/tmp/a.jsonl"))
            create.assert_called_once()
            self.assertEqual(mgr._headless_log_panes, {"e1": "%7"})
            # 2 回目は respawn で張り替え（ペインを増やさない）
            self.assertTrue(mgr.open_headless_log_pane("e1", "issues", "/tmp/b.jsonl"))
            create.assert_called_once()
        respawns = [c for c in tmux.call_args_list if c.args and c.args[0] == "respawn-pane"]
        self.assertEqual(len(respawns), 1)
        self.assertIn("/tmp/b.jsonl", respawns[0].args[-1])

    def test_empty_log_file_is_refused(self):
        self.assertFalse(self._mgr("sess:0").open_headless_log_pane("e1", "n", ""))


class ProgressRedirectTests(unittest.TestCase):
    """デーモンの headless スレッドでは進行表示をテキスト版ログへ振り向ける。

    デーモンの stdout はコントロールペインなので、print のままだと実行の様子
    （ラウンド・run・write_files・却下）が controller のログに混ざって流れる。
    ログペインが tail するのは `[tag] message` のテキスト版で、dashboard 定常業務の
    実行ペイン（`[run] …`）と同じ見え方にする（生 jsonl は見せない）。
    """

    def tearDown(self):
        tl._TL_PROGRESS_LOCAL.view_file = None

    def test_redirects_to_the_view_file_when_set(self):
        import io
        import tempfile
        with tempfile.NamedTemporaryFile("r", suffix=".log") as f:
            tl._TL_PROGRESS_LOCAL.view_file = f.name
            with mock.patch.object(al.sys, "stdout", io.StringIO()) as out:
                tl._tl_progress("ラウンド 1/8", "agent-loop")
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(f.read(), "[agent-loop] ラウンド 1/8\n")

    def test_prints_to_stdout_without_a_sink(self):
        import io
        with mock.patch.object(al.sys, "stdout", io.StringIO()) as out:
            tl._tl_progress("state: fetch", "statemachine")
        self.assertEqual(out.getvalue(), "[statemachine] state: fetch\n")

    def test_view_file_sits_next_to_the_jsonl(self):
        self.assertEqual(tl._tl_progress_view_file("/tmp/runs/headless/123-ab.jsonl"),
                         "/tmp/runs/headless/123-ab.log")


class StopKillsLogPanesTests(unittest.TestCase):
    def test_stop_kills_headless_log_panes(self):
        """quit 後に tail -F だけのペインを残さない。"""
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._lock = al.threading.Lock()
        mgr._panes = {}
        mgr._headless_log_panes = {"e1": "%9"}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr.remove_state = mock.Mock()
        with mock.patch.object(al, "_tmux_cmd") as tmux:
            mgr.stop()
        kills = [c.args for c in tmux.call_args_list if c.args and c.args[0] == "kill-pane"]
        self.assertEqual(kills, [("kill-pane", "-t", "%9")])
        self.assertEqual(mgr._headless_log_panes, {})


if __name__ == "__main__":
    unittest.main()
