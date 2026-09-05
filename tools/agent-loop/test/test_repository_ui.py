"""statemachine-maker などの薄い UI が使う agent-loop の機械可読境界。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as al  # noqa: E402


class RepositorySnapshotTest(unittest.TestCase):
    def test_workflows_and_their_schedules_are_returned_together(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "digest" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: 日次ダイジェスト\n"
                "description: 更新を短くまとめる\n"
                "context:\n"
                "  topic: ''\n"
                "states:\n"
                "  done:\n"
                "    terminal: true\n",
                encoding="utf-8",
            )
            config = root / ".agents" / "agent-loop.yml"
            config.parent.mkdir()
            config.write_text(
                "prompts:\n"
                "  - name: 朝のダイジェスト\n"
                "    statemachine: digest\n"
                "    agent_cli: codex\n"
                "    model: gpt-5\n"
                "    input:\n"
                "      topic: AI\n"
                "    cron: '30 9 * * 1,3'\n"
                "    enabled: true\n",
                encoding="utf-8",
            )

            snapshot = al.repository_snapshot(root)
            next_at = snapshot["machines"][0]["schedule"].pop("nextAt")
            self.assertRegex(next_at, r"^\d{4}-\d{2}-\d{2}T")

            self.assertEqual(snapshot["machines"], [{
                "machine": "digest",
                "workflow": ".statemachine/digest/workflow.yaml",
                "name": "日次ダイジェスト",
                "description": "更新を短くまとめる",
                "parameters": ["topic"],
                "schedule": {
                    "entryName": "朝のダイジェスト",
                    "enabled": True,
                    "kind": "weekly",
                    "time": "09:30",
                    "days": [1, 3],
                    "input": {"topic": "AI"},
                    "agentCli": "codex",
                    "model": "gpt-5",
                    "advanced": False,
                },
                "active": None,
                "history": [],
            }])
            self.assertEqual(snapshot["daemon"], {
                "running": False, "paused": False, "pid": None,
                "activeCount": 0, "queueDepth": 0,
            })

    def test_schedule_includes_the_next_run_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "report" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: report\nstates:\n  done:\n    terminal: true\n", encoding="utf-8")
            config = root / ".agents" / "agent-loop.yml"
            config.parent.mkdir()
            config.write_text(
                "prompts:\n  - name: report\n    statemachine: report\n"
                "    interval_minutes: 30\n",
                encoding="utf-8",
            )

            schedule = al.repository_snapshot(root)["machines"][0]["schedule"]

            self.assertRegex(schedule["nextAt"], r"^\d{4}-\d{2}-\d{2}T")

    def test_parameters_are_found_in_actions_without_exposing_runtime_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "report" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: レポート\n"
                "context:\n"
                "  fixed: 既定値\n"
                "states:\n"
                "  start:\n"
                "    action:\n"
                "      type: agent\n"
                "      text: '{{month}} の {{fixed}} を {{last_output}} からまとめる'\n"
                "    output_key: summary\n"
                "    transitions:\n"
                "      - to: done\n"
                "  done:\n"
                "    action:\n"
                "      type: agent\n"
                "      text: '{{summary}} を {{today}} に出力する'\n"
                "    terminal: true\n",
                encoding="utf-8",
            )

            machine = al.repository_snapshot(root)["machines"][0]

            self.assertEqual(machine["parameters"], ["month"])


class RepositoryScheduleTest(unittest.TestCase):
    def test_a_simple_schedule_is_saved_without_losing_other_entries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "review" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: レビュー\ncontext:\n  topic: ''\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            config = root / ".agents" / "agent-loop.yml"
            config.parent.mkdir()
            config.write_text(
                "mapping:\n  workspace:\n    main: /work\n"
                "prompts:\n"
                "  - name: 既存の点検\n"
                "    prompt: 点検する\n"
                "    interval_minutes: 60\n",
                encoding="utf-8",
            )

            result = al.update_repository_schedule(root, {
                "workflow": ".statemachine/review/workflow.yaml",
                "entryName": "平日のレビュー",
                "enabled": True,
                "schedule": {"kind": "weekly", "time": "18:05", "days": [1, 2, 3, 4, 5]},
                "input": {"topic": "変更点"},
            })

            stored = al._read_config_file(config)
            self.assertEqual(result, {
                "saved": True, "applied": False, "daemonRunning": False,
                "workflow": ".statemachine/review/workflow.yaml",
            })
            self.assertEqual(stored, {
                "mapping": {"workspace": {"main": "/work"}},
                "prompts": [
                    {"name": "既存の点検", "prompt": "点検する", "interval_minutes": 60},
                    {
                        "name": "平日のレビュー",
                        "statemachine": ".statemachine/review/workflow.yaml",
                        "input": {"topic": "変更点"},
                        "cron": "5 18 * * 1,2,3,4,5",
                        "enabled": True,
                    },
                ],
            })

    def test_missing_required_input_is_rejected_before_the_config_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "review" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: レビュー\ncontext:\n  topic: ''\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            config = root / ".agents" / "agent-loop.yml"
            config.parent.mkdir()
            original = "prompts:\n  - name: 既存\n    prompt: 維持する\n    interval_minutes: 10\n"
            config.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "topic"):
                al.update_repository_schedule(root, {
                    "workflow": ".statemachine/review/workflow.yaml",
                    "enabled": True,
                    "schedule": {"kind": "daily", "time": "09:00"},
                    "input": {},
                })

            self.assertEqual(config.read_text(encoding="utf-8"), original)


class RepositoryRunHistoryTest(unittest.TestCase):
    def test_manual_and_scheduled_results_share_the_machine_history(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as history_dir:
            root = Path(td)
            workflow = root / ".statemachine" / "review" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: レビュー\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"AGENT_LOOP_RUN_HISTORY_DIR": history_dir}):
                al.record_repository_run(root, {
                    "runId": "manual-1",
                    "workflow": ".statemachine/review/workflow.yaml",
                    "source": "manual",
                    "startedAt": "2026-09-05T01:00:00Z",
                    "finishedAt": "2026-09-05T01:02:00Z",
                    "ok": True,
                    "finalState": "done",
                    "logFile": str(root / ".statemachine-use" / "logs" / "one.jsonl"),
                })
                al.record_repository_run(root, {
                    "runId": "scheduled-1",
                    "workflow": ".statemachine/review/workflow.yaml",
                    "entryName": "夜のレビュー",
                    "source": "scheduled",
                    "startedAt": "2026-09-05T02:00:00Z",
                    "finishedAt": "2026-09-05T02:03:00Z",
                    "ok": False,
                    "escalate": True,
                    "finalState": "check",
                    "stopReason": "check_exhausted",
                })

                history = al.repository_snapshot(root)["machines"][0]["history"]

            self.assertEqual([item["runId"] for item in history], ["scheduled-1", "manual-1"])
            self.assertEqual(history[0]["source"], "scheduled")
            self.assertTrue(history[0]["escalate"])
            self.assertTrue(history[1]["ok"])

    def test_log_is_read_only_when_it_belongs_to_the_selected_history(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as history_dir:
            root = Path(td)
            workflow = root / ".statemachine" / "review" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: review\nstates:\n  done:\n    terminal: true\n", encoding="utf-8")
            log_file = root / ".statemachine-use" / "logs" / "run.jsonl"
            log_file.parent.mkdir(parents=True)
            log_file.write_text('{"event":"start"}\n{"event":"done"}\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"AGENT_LOOP_RUN_HISTORY_DIR": history_dir}):
                al.record_repository_run(root, {
                    "runId": "run-1", "workflow": ".statemachine/review/workflow.yaml",
                    "source": "manual", "ok": True, "logFile": str(log_file),
                })

                result = al.repository_run_log(root, {
                    "workflow": ".statemachine/review/workflow.yaml", "runId": "run-1",
                })

                self.assertIn('"event":"done"', result["text"])
                with self.assertRaisesRegex(ValueError, "ログ"):
                    al.repository_run_log(root, {
                        "workflow": ".statemachine/review/workflow.yaml", "runId": "unknown",
                    })


class RepositoryCliTest(unittest.TestCase):
    def test_inspect_prints_one_json_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "only" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: ひとつ\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            cli = Path(__file__).resolve().parents[1] / "agent-loop.py"

            completed = subprocess.run(
                [sys.executable, str(cli), "inspect", "--json", "--dir", str(root)],
                text=True, capture_output=True, check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["machines"][0]["machine"], "only")

    def test_schedule_accepts_json_on_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflow = root / ".statemachine" / "only" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: ひとつ\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            cli = Path(__file__).resolve().parents[1] / "agent-loop.py"
            request = {
                "workflow": ".statemachine/only/workflow.yaml",
                "enabled": True,
                "schedule": {"kind": "interval", "minutes": 45},
                "input": {},
            }

            completed = subprocess.run(
                [sys.executable, str(cli), "schedule", "--json", "--dir", str(root)],
                input=json.dumps(request), text=True, capture_output=True, check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["saved"])
            schedule = al.repository_snapshot(root)["machines"][0]["schedule"]
            self.assertEqual((schedule["kind"], schedule["minutes"]), ("interval", 45))

    def test_log_accepts_history_identity_on_stdin(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as history_dir:
            root = Path(td)
            workflow = root / ".statemachine" / "only" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: one\nstates:\n  done:\n    terminal: true\n", encoding="utf-8")
            log_file = root / ".statemachine-use" / "logs" / "one.jsonl"
            log_file.parent.mkdir(parents=True)
            log_file.write_text('{"event":"done"}\n', encoding="utf-8")
            cli = Path(__file__).resolve().parents[1] / "agent-loop.py"
            env = {**os.environ, "AGENT_LOOP_RUN_HISTORY_DIR": history_dir}
            with mock.patch.dict(os.environ, {"AGENT_LOOP_RUN_HISTORY_DIR": history_dir}):
                al.record_repository_run(root, {
                    "runId": "one", "workflow": ".statemachine/only/workflow.yaml",
                    "source": "manual", "ok": True, "logFile": str(log_file),
                })

            completed = subprocess.run(
                [sys.executable, str(cli), "log", "--json", "--dir", str(root)],
                input=json.dumps({"workflow": ".statemachine/only/workflow.yaml", "runId": "one"}),
                text=True, capture_output=True, check=False, env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"event":"done"', json.loads(completed.stdout)["text"])

    def test_manual_statemachine_command_records_its_result(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as history_dir:
            root = Path(td)
            workflow = root / ".statemachine" / "only" / "workflow.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: ひとつ\nstates:\n  done:\n    terminal: true\n",
                encoding="utf-8",
            )
            result = {
                "ok": True, "finalState": "done", "stopReason": "terminal_state",
                "logFile": str(root / ".statemachine-use" / "logs" / "manual.jsonl"),
                "files": [],
            }
            argv = ["agent-loop", "statemachine", "--workflow", ".statemachine/only/workflow.yaml",
                    "--dir", str(root), "--agent-cli", "fake"]
            fake_agent = {"cli": "fake", "model": "m", "spec": {}}
            with mock.patch.dict(os.environ, {"AGENT_LOOP_RUN_HISTORY_DIR": history_dir}), \
                 mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(al._harness_statemachine, "_sm_resolve_agent",
                                   return_value=fake_agent), \
                 mock.patch.object(al._harness_statemachine, "run_statemachine",
                                   return_value=result):
                with self.assertRaises(SystemExit) as stopped:
                    al.main()
                history = al.repository_snapshot(root)["machines"][0]["history"]

            self.assertEqual(stopped.exception.code, 0)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["source"], "manual")
            self.assertEqual(history[0]["finalState"], "done")


if __name__ == "__main__":
    unittest.main()
