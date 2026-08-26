"""headless 経路（対話ペインを持たない CLI での定期プロンプト実行）の回帰。

設計: docs/plans/2026-08-11-agent-loop-headless-agent-cli-design.md
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402
# ハーネスの実装は agentcore（agent-herd と共有する 1 実装）。agent_loop は
# 委譲するだけなので、差し替えも参照もそちらへ向ける。sys.path は
# agent_loop の import が通してくれる。
from agentcore.harness import toolloop as tl  # noqa: E402
from agentcore.tests.harnesspatch import patch_harness  # noqa: E402


def _write_cli(directory, name, spec):
    agents = Path(directory) / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")


class EntryRouteTest(unittest.TestCase):
    """entry ごとの agent_cli / model と実行経路の解決。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _write_cli(self.dir, "loopy", {
            "command": ["loopy", "run"], "headless_autonomy": "tool-loop",
            "interactive": {"command": ["loopy", "chat"]},
        })
        _write_cli(self.dir, "plain", {
            "command": ["plain", "run"], "headless_autonomy": "single-shot",
        })
        # control.json を空にして「管理面が宣言していない」状態を作る
        self.ctl = tempfile.mkdtemp()
        os.environ["AGENT_CONTROL_DIR"] = self.ctl
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}
        if hasattr(al, "_CACHE"):
            al._CACHE.clear()

    def _resolve(self, config, entry):
        return al.resolve_entry_profile(config, entry, project_dir=self.dir)

    def test_entry_fields_are_optional_and_fall_back_to_common(self):
        profile, route = self._resolve({"agent_cli": "loopy"}, {"name": "n"})
        self.assertEqual(profile.name, "loopy")
        self.assertEqual(route, "interactive")

    def test_entry_agent_cli_overrides_common(self):
        profile, route = self._resolve({"agent_cli": "loopy"},
                                       {"name": "n", "agent_cli": "plain"})
        self.assertEqual(profile.name, "plain")
        self.assertEqual(route, "per-run")          # 対話ペインを持たない → 強制 per-run

    def test_entry_model_only_keeps_common_cli(self):
        profile, _ = self._resolve({"agent_cli": "loopy"}, {"name": "n", "model": "m9"})
        self.assertEqual(profile.name, "loopy")
        self.assertEqual(profile.model, "m9")

    def test_session_per_run_uses_headless_route_for_interactive_cli(self):
        _, route = self._resolve({"agent_cli": "loopy"}, {"name": "n", "session": "per-run"})
        self.assertEqual(route, "per-run")

    def test_control_declaration_wins_over_entry(self):
        Path(self.ctl, "control.json").write_text(json.dumps({
            "version": 1, "revision": 3,
            "workloads": {"routine": {"agent_cli": "loopy"}},
        }), encoding="utf-8")
        al._CONTROL_CACHE["mtime"] = None
        # _apply_control_agent は「適用した revision」をプロセス全体へ控える（status の
        # revision_applied）。デーモンは 1 プロセス 1 起動なので実運用では正しいが、
        # テストでは他のテストへ漏れるので戻す。
        self.addCleanup(setattr, al, "_REVISION_APPLIED", al._REVISION_APPLIED)
        config = al._apply_control_agent({"agent_cli": "plain"})
        profile, _ = self._resolve(config, {"name": "n", "agent_cli": "plain"})
        self.assertEqual(profile.name, "loopy")     # 管理面の宣言を entry で上書きしない


class PaneRouteSyncTest(unittest.TestCase):
    """起動時のペイン事前作成が headless / entry 固有 CLI を考慮する回帰。

    以前は全 entry ぶんの対話ペインを無条件に起こしていたため、headless の
    agent_cli を設定していても kiro-cli が必ず立ち上がり、kiro-cli の無い環境では
    起動が RuntimeError で落ちていた。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _write_cli(self.dir, "loopy", {
            "command": ["loopy", "run"], "headless_autonomy": "tool-loop",
            "interactive": {"command": ["loopy", "chat"]},
        })
        _write_cli(self.dir, "plain", {
            "command": ["plain", "run"], "headless_autonomy": "single-shot",
        })
        self.ctl = tempfile.mkdtemp()
        os.environ["AGENT_CONTROL_DIR"] = self.ctl
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}

    def _sched(self, entries, tool_config=None):
        mgr = mock.Mock()
        sched = al.PeriodicScheduler(
            mgr, entries, workspace=self.dir, tool_config=tool_config or {})
        return sched, mgr

    def _synced(self, mgr):
        return mgr.sync_entries.call_args[0][0]

    def test_headless_common_cli_marks_entries_per_run(self):
        _, mgr = self._sched(
            [{"name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True}],
            tool_config={"agent_cli": "plain"})
        self.assertEqual(self._synced(mgr)[0].get("_pane_route"), "per-run")

    def test_entry_agent_cli_marks_entry_lazy(self):
        _, mgr = self._sched([{
            "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True,
            "agent_cli": "loopy",
        }])
        self.assertEqual(self._synced(mgr)[0].get("_pane_route"), "lazy")

    def test_default_entry_has_no_pane_route(self):
        _, mgr = self._sched(
            [{"name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True}])
        self.assertNotIn("_pane_route", self._synced(mgr)[0])

    def _session_mgr(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._lock = threading.Lock()
        mgr._panes = {}
        mgr._owners = {}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._tuning_profiles = {}
        mgr._start_pane = mock.Mock()
        mgr._stop_pane = mock.Mock()
        mgr.write_state = mock.Mock()
        return mgr

    def test_sync_entries_skips_per_run_and_lazy_panes(self):
        mgr = self._session_mgr()
        mgr.sync_entries([
            {"id": "a", "name": "a", "_pane_route": "per-run"},
            {"id": "b", "name": "b", "_pane_route": "lazy"},
            {"id": "c", "name": "c"},
        ])
        started = [call.args[0] for call in mgr._start_pane.call_args_list]
        self.assertEqual(started, ["c"])

    def test_sync_entries_keeps_existing_lazy_pane(self):
        mgr = self._session_mgr()
        mgr._panes = {"b": "%9"}
        mgr._owners = {"b": "scheduled"}
        mgr._prompt_names = {"b": "b"}
        mgr.sync_entries([{"id": "b", "name": "b", "_pane_route": "lazy"}])
        mgr._stop_pane.assert_not_called()
        mgr._start_pane.assert_not_called()

    def test_sync_entries_stops_pane_when_entry_turns_per_run(self):
        mgr = self._session_mgr()
        mgr._panes = {"a": "%9"}
        mgr._owners = {"a": "scheduled"}
        mgr._prompt_names = {"a": "a"}
        mgr.sync_entries([{"id": "a", "name": "a", "_pane_route": "per-run"}])
        mgr._stop_pane.assert_called_once_with("a")


class EntryValidationTest(unittest.TestCase):
    """prompts entry の新フィールドの検証。"""

    def _one(self, **extra):
        return al.validate_entries([{
            "name": "n", "prompt": "p", "interval_minutes": 10, "enabled": True, **extra,
        }])[0]

    def test_defaults_keep_existing_behaviour(self):
        entry = self._one()
        self.assertIsNone(entry["agent_cli"])
        self.assertIsNone(entry["model"])
        self.assertEqual(entry["session"], "keep")   # 既存設定は無改変で従来どおり
        self.assertEqual(entry["acceptance"], [])

    def test_acceptance_accepts_list_and_scalar(self):
        self.assertEqual(self._one(acceptance=["a", " b "])["acceptance"], ["a", "b"])
        self.assertEqual(self._one(acceptance="only")["acceptance"], ["only"])

    def test_acceptance_rejects_wrong_shape(self):
        with self.assertRaises(ValueError):
            self._one(acceptance=[1, 2])

    def test_session_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            self._one(session="forever")


class AcceptanceGateTest(unittest.TestCase):
    """受入条件を入力にした機械層の証跡ゲート（LLM を介さない）。"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.acc = ["`out/report.md` が今回の実行で更新されている", "件数付きで並んでいる"]
        self.target = os.path.join(self.dir, "out", "report.md")

    def _errors(self, touched, before):
        return tl.acceptance_evidence_errors(self.acc, cwd=self.dir, touched=touched,
                                             stamps_before=before)

    def test_only_backquoted_project_paths_are_extracted(self):
        found = tl.acceptance_paths(self.acc, self.dir)
        self.assertEqual(found, [self.target])      # 散文の語をパスと誤認しない

    def test_backquoted_command_names_are_not_paths(self):
        # 受入条件の地の文にはコマンド名も同じ記法で出る。パスとして拾うと永久に
        # 満たせない条件になり、成果物を書き終えた実行まで fail する。
        self.assertEqual(tl.acceptance_paths(
            ["`agent-audit` の出力に無い件数を書いていない",
             "`agent-audit usage --period day` を実行した"], self.dir), [])

    def test_paths_outside_the_workspace_are_ignored(self):
        self.assertEqual(tl.acceptance_paths(["`../../etc/passwd` を読む"], self.dir), [])

    def test_missing_file_fails(self):
        before = tl.acceptance_stamps(self.acc, self.dir)
        self.assertTrue(self._errors(set(), before))

    def test_self_reported_but_untouched_file_fails(self):
        before = tl.acceptance_stamps(self.acc, self.dir)
        os.makedirs(os.path.dirname(self.target))
        Path(self.target).write_text("x", encoding="utf-8")
        self.assertTrue(self._errors(set(), before))   # 偽 done を止める

    def test_unchanged_file_fails(self):
        os.makedirs(os.path.dirname(self.target))
        Path(self.target).write_text("x", encoding="utf-8")
        before = tl.acceptance_stamps(self.acc, self.dir)
        self.assertTrue(self._errors({self.target}, before))

    def test_timestamp_only_touch_does_not_count_as_change(self):
        os.makedirs(os.path.dirname(self.target))
        Path(self.target).write_text("x", encoding="utf-8")
        before = tl.acceptance_stamps(self.acc, self.dir)
        stat = os.stat(self.target)
        os.utime(self.target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertTrue(self._errors({self.target}, before),
                        "内容が同じなら更新時刻だけ変わっても受入条件を満たさない")

    def test_touched_and_changed_passes(self):
        before = tl.acceptance_stamps(self.acc, self.dir)
        os.makedirs(os.path.dirname(self.target))
        Path(self.target).write_text("x", encoding="utf-8")
        self.assertEqual(self._errors({self.target}, before), [])

    def test_no_acceptance_means_nothing_is_verified(self):
        self.assertEqual(tl.acceptance_evidence_errors([], cwd=self.dir, touched=set(),
                                                       stamps_before={}), [])

    def test_criteria_without_a_quoted_path_are_not_verified(self):
        # 「書いてある」と「機械で照合できる」は別。グロブや文章だけの条件は判定層待ちで、
        # ここを検証済みにすると何も照合していない実行が done の根拠になる。
        self.assertFalse(tl._tl_verified(["要約ファイルが *.md 形式で作成されていること"], self.dir))
        self.assertFalse(tl._tl_verified([], self.dir))
        self.assertTrue(tl._tl_verified(["`out/report.md` が更新されている"], self.dir))


class GoalToolLoopTest(unittest.TestCase):
    """ツールループ非内蔵の CLI へツール実行を供給する run_goal。"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.log = os.path.join(self.dir, "log.jsonl")
        # ハーネスの差し替えは agentcore 側のモジュールへ当てる（agent_loop は委譲層で、
        # そこへ setattr しても本文には届かない）。後始末は ExitStack に任せる。
        self._harness = contextlib.ExitStack()
        self.addCleanup(self._harness.close)

    def _script(self, steps):
        it = iter(steps)

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            out = next(it)
            if not readonly and files:      # write モード: 実ファイルを書く CLI の代役
                for f in files:
                    Path(f).write_text("generated\n", encoding="utf-8")
            return out
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

    def test_write_files_then_final_passes_the_gate(self):
        self._script(['{"type":"write_files","paths":["out.md"]}', "wrote",
                      '{"type":"final","output":"done"}'])
        result = tl.run_goal(goal="out.md を書く", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=["`out.md` が更新されている"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])

    def test_machine_gate_pass_ends_the_loop_without_final(self):
        # 小型モデルは書き終えても final を出さず、同じ write_files を繰り返して
        # ラウンド上限まで走る（実測: 1 回で書けた仕事に 8 ラウンド）。
        # done の根拠は元から機械検証だけなので、その PASS を停止条件にする。
        calls = []

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            calls.append(readonly)
            if not readonly:
                for f in files:
                    Path(f).write_text("generated\n", encoding="utf-8")
                return "wrote"
            return '{"type":"write_files","paths":["out.md"]}'
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        result = tl.run_goal(goal="out.md を書く", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=["`out.md` が更新されている"])
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2, "1 ラウンド（要求 + 書き込み）で止まる")

    def test_without_checkable_criteria_the_loop_still_waits_for_final(self):
        # 照合できる条件が無ければ止める根拠も無いので、従来どおり final を待つ。
        self._script(['{"type":"write_files","paths":["out.md"]}', "wrote",
                      '{"type":"final","output":"done"}'])
        result = tl.run_goal(goal="out.md を書く", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=[])
        self.assertEqual(result["output"], "done")

    def test_final_without_any_work_is_rejected(self):
        self._script(['{"type":"final","output":"done"}'])
        result = tl.run_goal(goal="out2.md を作る", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=["`out2.md` を作る"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["evidenceErrors"])

    def test_without_acceptance_the_machine_layer_verifies_nothing(self):
        self._script(['{"type":"final","output":"done"}'])
        result = tl.run_goal(goal="なにかする", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=[])
        self.assertTrue(result["ok"])
        self.assertFalse(result["verified"])    # done の根拠にしない

    def test_each_round_and_every_rejection_is_visible(self):
        # ローカルモデルは 1 周に数十秒かかる。却下が黙って次の周へ行くと、外からは
        # 「止まっている」ようにしか見えず、人は打ち切りも修正もできない。
        self._script(['{"type":"read_files","paths":["missing.txt"]}',
                      '{"type":"final","output":"done"}'])
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            tl.run_goal(goal="何かする", cwd=self.dir, agent={}, log_file=self.log,
                        acceptance=[], tag="run")
        printed = out.getvalue()
        self.assertIn("ラウンド 1/", printed)
        self.assertIn("ラウンド 2/", printed)
        self.assertIn("却下: 読み取り対象がありません: missing.txt", printed)
        events = [json.loads(line) for line in
                  Path(self.log).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(e.get("event") == "rejected" for e in events),
                        "却下は実行ログにも残す（後から why を追える）")

    def test_first_round_does_not_claim_a_command_already_ran(self):
        # 無条件に「実行済みだから走らせるな」と書いていた頃、モデルは実行していない
        # コマンドを「実行した」と書いた。成功した run が履歴に入るまでは言わない。
        prompts = []

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            prompts.append(prompt)
            if len(prompts) == 1:
                return '{"type":"run","command":"echo","args":["hi"],"timeout_sec":10}'
            return '{"type":"final","output":"done"}'
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        tl.run_goal(goal="echo する", cwd=self.dir, agent={}, log_file=self.log, acceptance=[])
        self.assertNotIn("already completed", prompts[0])
        self.assertIn("already completed", prompts[1])

    def test_write_call_receives_the_tool_results(self):
        # コマンドの出力を渡さないまま書かせると、モデルは中身を創作する
        # （実測: 集計に 3 グループ出ているのに「データなし」と書いた）。
        prompts = []

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            prompts.append(prompt)
            for f in files:
                Path(f).write_text("generated\n", encoding="utf-8")
            if files:
                return "wrote"
            if len(prompts) == 1:
                return '{"type":"run","command":"echo","args":["FACT-42"],"timeout_sec":10}'
            return '{"type":"write_files","paths":["out.md"]}'
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        tl.run_goal(goal="out.md に書く", cwd=self.dir, agent={}, log_file=self.log,
                    acceptance=["`out.md` が更新されている"])
        self.assertIn("FACT-42", prompts[-1])

    def test_failed_run_cannot_pass_via_a_later_write(self):
        # コマンド失敗後に成果物へ触れただけで done になっていた実機回帰。
        self._script([
            '{"type":"run","command":"agent-audit","args":["--period","day"]}',
            '{"type":"write_files","paths":["out.md"]}',
            "wrote",
        ])
        failed = {"status": 2, "error": "", "stdout": "", "stderr": "invalid command"}
        with (patch_harness("_tl_executable_on_path",
                            return_value="/usr/bin/agent-audit"),
              patch_harness("_tl_exec_argv", return_value=failed)):
            result = tl.run_goal(
                goal="agent-audit の結果を out.md に書く", cwd=self.dir,
                agent={}, log_file=self.log,
                acceptance=["`out.md` が更新されている"], max_rounds=2)
        self.assertFalse(result["ok"])
        self.assertTrue(any("コマンド実行が失敗" in error
                            for error in result["evidenceErrors"]))

    def test_successful_retry_resolves_a_failed_run(self):
        self._script([
            '{"type":"run","command":"agent-audit","args":["--period","day"]}',
            '{"type":"run","command":"agent-audit","args":["usage","--period","day"]}',
            '{"type":"write_files","paths":["out.md"]}',
            "wrote",
        ])
        failed = {"status": 2, "error": "", "stdout": "", "stderr": "invalid command"}
        passed = {"status": 0, "error": "", "stdout": "no records", "stderr": ""}
        with (patch_harness("_tl_executable_on_path",
                            return_value="/usr/bin/agent-audit"),
              patch_harness("_tl_exec_argv", side_effect=[failed, passed])):
            result = tl.run_goal(
                goal="agent-audit の結果を out.md に書く", cwd=self.dir,
                agent={}, log_file=self.log,
                acceptance=["`out.md` が更新されている"], max_rounds=3)
        self.assertTrue(result["ok"])

    def test_control_rounds_use_the_declared_variant(self):
        # 制御応答は編集能力の要らない周。定義が variants.planner を申告していれば、その周
        # だけ JSON 用の起動形へ振り替える（編集は元の CLI のまま）。
        _write_cli(self.dir, "editor", {
            "command": ["editor"], "prompt_via": "argv", "prompt_flag": "--message",
            "file_flag": "--file", "read_flag": "--read", "variants": {"planner": "jsonner"},
            "headless_autonomy": "single-shot",
        })
        _write_cli(self.dir, "jsonner", {
            "command": ["jsonner"], "prompt_via": "stdin",
            "headless_autonomy": "single-shot",
        })
        seen = []

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            seen.append(agent["cli"])
            for f in files:
                Path(f).write_text("generated\n", encoding="utf-8")
            return "wrote" if files else '{"type":"write_files","paths":["out.md"]}'
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        agent = tl._tl_resolve_agent("editor", "", self.dir)
        tl.run_goal(goal="out.md を書く", cwd=self.dir, agent=agent, log_file=self.log,
                    acceptance=["`out.md` が更新されている"])
        self.assertEqual(seen, ["jsonner", "editor"])

    def test_control_agent_falls_back_when_the_variant_is_missing(self):
        _write_cli(self.dir, "editor2", {
            "command": ["editor2"], "prompt_via": "argv", "prompt_flag": "--message",
            "variants": {"planner": "nonexistent"}, "headless_autonomy": "single-shot",
        })
        agent = tl._tl_resolve_agent("editor2", "", self.dir)
        self.assertIs(tl._tl_control_agent(agent, self.dir), agent)

    def test_off_contract_reply_writes_the_declared_deliverable(self):
        # 小型モデルは材料が揃うと JSON をやめて本文を書き始める。宣言済みの成果物が
        # 未着手なら、そのラウンドを捨てずに write へ回す。
        self._script(["ここに要約を書きました（JSON ではない）", "wrote"])
        result = tl.run_goal(goal="out.md に書く", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=["`out.md` が更新されている"])
        self.assertTrue(result["ok"])
        events = [json.loads(line) for line in
                  Path(self.log).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(e.get("event") == "fallback_write" for e in events))

    def test_off_contract_reply_without_a_deliverable_is_still_rejected(self):
        self._script(["JSON ではない", '{"type":"final","output":"done"}'])
        tl.run_goal(goal="何かする", cwd=self.dir, agent={}, log_file=self.log, acceptance=[])
        events = [json.loads(line) for line in
                  Path(self.log).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(e.get("event") == "rejected" for e in events))

    def test_shell_requests_are_refused_and_reported_back(self):
        self._script(['{"type":"run","command":"bash","args":["-c","rm -rf /"]}',
                      '{"type":"final","output":"done"}'])
        result = tl.run_goal(goal="何かする", cwd=self.dir, agent={}, log_file=self.log,
                             acceptance=[])
        events = [json.loads(line) for line in
                  Path(self.log).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(result["ok"])            # 却下を伝えたうえでループは続く
        self.assertFalse(any(e.get("event") == "start" for e in events))  # 実行はしない


class HeadlessDispatchTest(unittest.TestCase):
    """dispatch から headless 実行までの通し。"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        _write_cli(self.dir, "plain", {
            "command": ["plain", "--message"], "prompt_via": "argv",
            "prompt_flag": "--message", "file_flag": "--file", "read_flag": "--read",
            "headless_autonomy": "single-shot",
        })
        os.environ["AGENT_CONTROL_DIR"] = tempfile.mkdtemp()
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}
        # ハーネスの差し替えは agentcore 側のモジュールへ当てる（agent_loop は委譲層で、
        # そこへ setattr しても本文には届かない）。後始末は ExitStack に任せる。
        self._harness = contextlib.ExitStack()
        self.addCleanup(self._harness.close)

    def _scheduler(self, entry):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._owners = {}
        mgr._lock = threading.Lock()
        mgr._target_path = self.dir
        mgr.sync_entries = mock.Mock()
        mgr.set_state_extras = mock.Mock()
        return al.PeriodicScheduler(mgr, [entry], workspace=self.dir, tool_config={})

    def test_single_shot_cli_runs_through_the_tool_loop(self):
        steps = iter(['{"type":"write_files","paths":["out.md"]}', "wrote",
                      '{"type":"final","output":"done"}'])

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            out = next(steps)
            if not readonly and files:
                for f in files:
                    Path(f).write_text("summary\n", encoding="utf-8")
            return out
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        entry = {"id": "e1", "name": "要約", "prompt": "`out.md` に要約を書く",
                 "interval_minutes": 10, "enabled": True, "agent_cli": "plain",
                 "acceptance": ["`out.md` が更新されている"]}
        sched = self._scheduler(entry)
        req = al.make_dispatch_request(source="schedule", entry_id="e1",
                                       prompt=entry["prompt"], cwd=self.dir)
        self.assertEqual(sched._try_dispatch_request(req), "done")
        for _ in range(100):
            time.sleep(0.02)
            if sched._active_count == 0 and os.path.exists(os.path.join(self.dir, "out.md")):
                break
        self.assertEqual(Path(self.dir, "out.md").read_text(encoding="utf-8").strip(), "summary")
        self.assertEqual(sched._active_count, 0)    # スロットと active が戻る


class RunSubcommandTest(unittest.TestCase):
    """`agent-loop run`: デーモン無しの単発実行。層の分岐はデーモンと同じ 1 実装を通る。"""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        _write_cli(self.dir, "plain", {
            "command": ["plain", "--message"], "prompt_via": "argv",
            "prompt_flag": "--message", "file_flag": "--file", "read_flag": "--read",
            "headless_autonomy": "single-shot",
        })
        os.environ["AGENT_LOOP_RUN_DIR"] = tempfile.mkdtemp()
        # ハーネスの差し替えは agentcore 側のモジュールへ当てる（agent_loop は委譲層で、
        # そこへ setattr しても本文には届かない）。後始末は ExitStack に任せる。
        self._harness = contextlib.ExitStack()
        self.addCleanup(self._harness.close)

    def _args(self, prompt, **kw):
        return al.argparse.Namespace(prompt=[prompt], agent_cli="plain", model=None,
                                     acceptance=kw.get("acceptance", []), dir=self.dir)

    def _run(self, args):
        """cmd_run を走らせ、(exit code, RESULT の JSON) を返す。"""
        out = io.StringIO()
        with mock.patch("sys.stdout", out), self.assertRaises(SystemExit) as exit_info:
            tl.cmd_run(args, Path(self.dir))
        line = [x for x in out.getvalue().splitlines() if x.startswith("RESULT ")][-1]
        return exit_info.exception.code, json.loads(line[len("RESULT "):])

    def test_single_shot_cli_gets_the_tool_loop_and_reports_result(self):
        steps = iter(['{"type":"write_files","paths":["out.md"]}', "wrote",
                      '{"type":"final","output":"done"}'])

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            out = next(steps)
            if not readonly and files:
                for f in files:
                    Path(f).write_text("summary\n", encoding="utf-8")
            return out
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))

        code, result = self._run(self._args("`out.md` に要約を書く",
                                            acceptance=["`out.md` が更新されている"]))
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])       # 受入条件があるので機械層が検証した
        self.assertEqual(Path(self.dir, "out.md").read_text(encoding="utf-8").strip(), "summary")

    def test_without_acceptance_the_run_is_recorded_as_unverified(self):
        self._harness.enter_context(patch_harness(
            "_tl_run_agent", side_effect=lambda *a, **kw: '{"type":"final","output":"done"}'))
        code, result = self._run(self._args("何かする"))
        self.assertEqual(code, 0)
        self.assertFalse(result["verified"])      # done の根拠にしない

    def test_acceptance_without_a_quoted_path_is_also_unverified(self):
        self._harness.enter_context(patch_harness(
            "_tl_run_agent", side_effect=lambda *a, **kw: '{"type":"final","output":"done"}'))
        code, result = self._run(self._args(
            "何かする", acceptance=["要約ファイルが *.md 形式で作成されていること"]))
        self.assertEqual(code, 0)
        self.assertFalse(result["verified"],
                         "条件が書いてあっても照合できなければ「検証なし」")

    def test_empty_prompt_is_rejected(self):
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as exit_info:
            tl.cmd_run(self._args("   "), Path(self.dir))
        self.assertEqual(exit_info.exception.code, 2)

    def test_prompt_can_be_a_file_in_the_working_directory(self):
        Path(self.dir, "task.md").write_text("ファイルに書いた指示", encoding="utf-8")
        seen = {}

        def fake(agent, prompt, *, cwd, readonly, read_files, files, log_file):
            seen["prompt"] = prompt
            return '{"type":"final","output":"done"}'
        self._harness.enter_context(patch_harness("_tl_run_agent", side_effect=fake))
        self._run(self._args("task.md"))
        self.assertIn("ファイルに書いた指示", seen["prompt"])

    def test_tool_loop_cli_is_invoked_once_without_the_harness(self):
        _write_cli(self.dir, "loopy", {"command": ["loopy", "--message"],
                                       "prompt_via": "argv", "prompt_flag": "--message",
                                       "headless_autonomy": "tool-loop"})
        calls = []

        def fake_exec(command, args, **kw):
            calls.append(command)
            return {"status": 0, "error": "", "stdout": "done", "stderr": ""}
        with patch_harness("_tl_exec_argv", fake_exec):
            args = self._args("何かする")
            args.agent_cli = "loopy"
            code, result = self._run(args)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["loopy"])        # ツールループを挟まず 1 回だけ呼ぶ


class HeadlessStartupCheckTest(unittest.TestCase):
    """起動時点検: 層3 × 受入条件なしは警告のみ、headless 非対応機能は致命。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _write_cli(self.dir, "plain", {"command": ["plain"],
                                       "headless_autonomy": "single-shot"})
        _write_cli(self.dir, "loopy", {"command": ["loopy"],
                                       "headless_autonomy": "tool-loop"})
        os.environ["AGENT_CONTROL_DIR"] = tempfile.mkdtemp()
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}

    def _check(self, entry):
        return al.check_headless_entries({"agent_cli": "plain"}, [entry], project_dir=self.dir)

    def test_missing_acceptance_only_warns(self):
        with self.assertLogs(al.log, level="WARNING") as logs:
            self.assertEqual(self._check({"name": "n", "prompt": "p"}), [])
        self.assertTrue(any("受入条件" in m for m in logs.output))

    def test_tool_loop_cli_without_acceptance_is_silent(self):
        problems = al.check_headless_entries(
            {"agent_cli": "loopy"}, [{"name": "n", "prompt": "p", "session": "per-run"}],
            project_dir=self.dir)
        self.assertEqual(problems, [])

    def test_ralph_and_external_target_are_fatal(self):
        self.assertTrue(self._check({"name": "n", "prompt": "p", "mode": "ralph"}))
        self.assertTrue(self._check({"name": "n", "prompt": "p", "target": "other"}))


if __name__ == "__main__":
    unittest.main()
