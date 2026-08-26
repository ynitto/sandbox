"""定期プロンプトの `statemachine:` 実行の回帰。

entry がステートマシンを宣言したら、デーモンは対話ペインへ本文を送るのではなく
ハーネスのステートマシン実行へ回す。実行条件（`input:` のマップと、自由文としての
`prompt`）の読み方は agent-herd・dashboard と同じ 1 実装（agentcore.loopentry）。

仕様: docs/specs/agent-loop-spec.md §2.3 / §3.5。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402
from agentcore.harness import statemachine as sm  # noqa: E402


def _write_cli(directory, name, spec):
    agents = Path(directory) / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")


class EntryValidationTest(unittest.TestCase):
    """読み込み時の正規化と、噛み合わない組合せの fail-closed。"""

    def _one(self, **extra):
        return al.validate_entries([{
            "name": "sm", "statemachine": "digest", "interval_minutes": 10, **extra,
        }])[0]

    def test_a_declaration_is_normalized_to_a_relative_workflow_path(self):
        entry = self._one(input={"topic": "llm"})
        self.assertEqual(entry["statemachine"], ".statemachine/digest/workflow.yaml")
        self.assertEqual(entry["input"], {"topic": "llm"})

    def test_conditions_alone_are_enough_to_keep_the_entry(self):
        # prompt / hooks / slash がひとつも無くても落とさない——実行条件は input: で足りる。
        entries = al.validate_entries([{
            "name": "sm", "statemachine": "digest", "input": {"topic": "llm"},
            "interval_minutes": 10,
        }])
        self.assertEqual(len(entries), 1)

    def test_a_statemachine_entry_is_a_per_run_session(self):
        self.assertEqual(self._one()["session"], "per-run")

    def test_an_explicit_keep_session_is_refused_instead_of_silently_flipped(self):
        with self.assertRaises(ValueError):
            self._one(session="keep")

    def test_features_that_need_an_interactive_pane_are_refused(self):
        for extra in ({"oneshot": True}, {"clean_session": 3}, {"target": "reviewer"},
                      {"slash": "summarize-logs"},
                      {"mode": "ralph", "max_iterations": 3}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    self._one(**extra)

    def test_acceptance_is_refused_because_the_workflow_declares_the_checks(self):
        with self.assertRaises(ValueError) as ctx:
            self._one(acceptance=["`out/x.md` がある"])
        self.assertIn("check", str(ctx.exception))

    def test_a_broken_declaration_stops_the_load_instead_of_dropping_the_entry(self):
        with self.assertRaises(ValueError):
            al.validate_entries([{"name": "sm", "statemachine": "../elsewhere",
                                  "interval_minutes": 10}])

    def test_entries_without_the_key_are_untouched(self):
        entry = al.validate_entries([{"name": "n", "prompt": "p",
                                      "interval_minutes": 10}])[0]
        self.assertIsNone(entry["statemachine"])
        self.assertIsNone(entry["input"])
        self.assertEqual(entry["session"], "keep")


class RouteTest(unittest.TestCase):
    """宣言があれば対話 CLI でもハーネス（per-run）へ回す。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _write_cli(self.dir, "loopy", {
            "command": ["loopy", "run"], "headless_autonomy": "tool-loop",
            "interactive": {"command": ["loopy", "chat"]},
        })
        self.ctl = tempfile.mkdtemp()
        os.environ["AGENT_CONTROL_DIR"] = self.ctl
        al._CONTROL_CACHE["mtime"] = None
        al._CONTROL_CACHE["data"] = {}

    def test_an_interactive_cli_still_takes_the_harness_route(self):
        _, route = al.resolve_entry_profile(
            {"agent_cli": "loopy"},
            {"name": "sm", "statemachine": ".statemachine/digest/workflow.yaml"},
            project_dir=self.dir)
        self.assertEqual(route, "per-run")

    def test_no_resolvable_cli_still_takes_the_harness_route(self):
        # agent_cli を書いていない（従来の kiro 経路の）設定でも、対話ペインへ倒さない
        # ——倒すと本文だけがペインへ流れ、ワークフローが一度も実行されない。
        sched = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        sched._workspace = self.dir
        sched._tool_config = {}
        sched._launch_drift = {}
        _, route = sched._entry_route(
            {"name": "sm", "statemachine": ".statemachine/digest/workflow.yaml"})
        self.assertEqual(route, "per-run")

    def test_a_missing_workflow_stops_the_daemon_at_startup(self):
        problems = al.check_headless_entries(
            {"agent_cli": "loopy"},
            [{"name": "sm", "statemachine": ".statemachine/none/workflow.yaml",
              "cwd": self.dir}],
            project_dir=self.dir)
        self.assertTrue(problems)
        self.assertIn("ステートマシン定義が見つかりません", problems[0])

    def test_an_existing_workflow_passes_the_startup_check(self):
        wf = Path(self.dir, ".statemachine", "digest")
        wf.mkdir(parents=True)
        (wf / "workflow.yaml").write_text("states: {}\n", encoding="utf-8")
        self.assertEqual(al.check_headless_entries(
            {"agent_cli": "loopy"},
            [{"name": "sm", "statemachine": ".statemachine/digest/workflow.yaml",
              "cwd": self.dir}],
            project_dir=self.dir), [])


class DispatchTest(unittest.TestCase):
    """dispatch がハーネスへ渡す実行条件。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sched = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        self.sched._lock = threading.RLock()
        self.sched._workspace = self.dir
        self.sched._tool_config = {"headless_pane": False}
        self.sched._session_mgr = mock.Mock()
        self.sched._semaphore = None
        self.sched._slot_monitor = None
        self.sched._executions = {}
        self.sched._sessions = {}
        self.sched._begin_active = mock.Mock()
        self.sched._end_active = mock.Mock()
        self.sched._release_slot = mock.Mock()
        self.sched._fail_execution = mock.Mock()

    def _run(self, entry, prompt, result):
        req = {"id": "r1", "entry_id": "e1", "prompt": prompt, "meta": {}}
        profile = mock.Mock(name_="p")
        profile.name = "loopy"
        profile.model = "m9"
        profile.autonomy = "tool-loop"
        seen = {}

        def fake_run_statemachine(**kwargs):
            seen.update(kwargs)
            return result

        with mock.patch.object(sm, "run_statemachine", fake_run_statemachine), \
             mock.patch.object(al._harness_toolloop, "_tl_resolve_agent",
                               lambda cli, model, cwd: {"cli": cli, "model": model}), \
             mock.patch.object(al._harness_toolloop, "run_prompt") as run_prompt:
            self.sched._run_headless(req, entry, {"prompt": prompt}, profile,
                                     self.dir, "r1", None)
        return seen, run_prompt

    def _entry(self, **extra):
        return {"id": "e1", "name": "sm", "acceptance": [],
                "statemachine": ".statemachine/digest/workflow.yaml", **extra}

    def test_the_declared_conditions_reach_the_harness(self):
        seen, run_prompt = self._run(
            self._entry(input={"topic": "llm"}), "今日の要約",
            {"ok": True, "finalState": "done", "logFile": "l", "files": []})
        run_prompt.assert_not_called()      # プロンプト実行の経路へは落ちない
        self.assertEqual(seen["workflow_path"], ".statemachine/digest/workflow.yaml")
        self.assertEqual(seen["parameters"], {"topic": "llm", "input": "今日の要約"})
        self.assertEqual(seen["cwd"], self.dir)
        self.sched._end_active.assert_called_once()
        self.sched._fail_execution.assert_not_called()

    def test_the_dispatched_prompt_is_the_free_text_condition(self):
        # フックが本文を決めた実行。entry の prompt ではなく届いた本文が条件になる。
        seen, _ = self._run(self._entry(prompt="設定の既定"), "フックの本文",
                            {"ok": True, "finalState": "done", "files": []})
        self.assertEqual(seen["parameters"], {"input": "フックの本文"})

    def test_a_failed_run_is_reported_as_a_statemachine_failure(self):
        self._run(self._entry(), "", {"ok": False, "finalState": "x", "files": []})
        self.sched._fail_execution.assert_called_once()
        self.assertEqual(self.sched._fail_execution.call_args.kwargs["reason"],
                         "statemachine_failed")

    def test_an_entry_without_a_declaration_still_runs_the_prompt_harness(self):
        req = {"id": "r1", "entry_id": "e1", "prompt": "本文", "meta": {}}
        profile = mock.Mock()
        profile.name = "loopy"
        profile.model = ""
        profile.autonomy = "tool-loop"
        with mock.patch.object(al._harness_toolloop, "_tl_resolve_agent",
                               lambda cli, model, cwd: {"cli": cli, "model": model}), \
             mock.patch.object(al._harness_toolloop, "run_prompt",
                               return_value={"ok": True, "verified": True}) as run_prompt, \
             mock.patch.object(sm, "run_statemachine") as run_sm:
            self.sched._run_headless(req, {"id": "e1", "name": "n", "acceptance": []},
                                     {"prompt": "本文"}, profile, self.dir, "r1", None)
        run_prompt.assert_called_once()
        run_sm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
