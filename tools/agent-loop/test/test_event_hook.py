#!/usr/bin/env python3
"""event hook の同時更新と配送保留の回帰テスト。"""
import collections
import importlib.util
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import agent_loop as al  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load("gitlab_issue_hook_test", HERE.parent / "hooks" / "gitlab-issue-hook.py")
resource_hook = _load(
    "resource_control_hook_test", HERE.parent / "hooks" / "resource-control-hook.py"
)
calibrate_hook = _load(
    "audit_calibrate_hook_test", HERE.parent / "hooks" / "audit-calibrate-hook.py"
)
memory_hook = _load(
    "memory_maintenance_hook_test", HERE.parent / "hooks" / "memory-maintenance-hook.py"
)


def _scheduler(entries):
    scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
    scheduler._session_mgr = types.SimpleNamespace(
        ensure_session=mock.Mock(return_value=True),
        get_pane_id=mock.Mock(return_value="%1"),
        send_prompt=mock.Mock(return_value=True),
        restart_pane=mock.Mock(),
        sync_entries=mock.Mock(),
        set_state_extras=mock.Mock(),
        _lock=al.threading.Lock(),
        _prompt_cwds={},
        _panes={"p1": "%1"},
    )
    scheduler._semaphore = None
    scheduler._slot_monitor = None
    scheduler._entries = entries
    scheduler._external_queues = {}
    scheduler._lock = al.threading.Lock()
    scheduler._stop_event = mock.Mock()
    scheduler._stop_event.wait.side_effect = [False, True]
    scheduler._node_budget_warned_at = 0.0
    scheduler._pending = []
    scheduler._debouncer = mock.Mock()
    scheduler._debouncer.is_duplicate.return_value = False
    scheduler._draining = False
    scheduler._run_state = "run"
    scheduler._reload_entries = None
    scheduler._hook_quarantine = set()
    scheduler._active_count = 0
    scheduler._active_ids = set()
    scheduler._inflight_ack_paths = set()
    scheduler._health = {}
    scheduler._input_recovery = False
    scheduler._mem_ok_streak = 0
    scheduler._mem_paused = False
    scheduler._workspace = "/tmp"
    scheduler._preflight_cache = {}
    scheduler._hook_cache = {}
    scheduler._hook_cache_lock = al.threading.Lock()
    return scheduler


def _run_once(scheduler):
    with mock.patch.object(al, "_control_lifecycle", return_value="run"), \
         mock.patch.object(al, "_node_budget_state", return_value=None), \
         mock.patch.object(al, "_write_status"), \
         mock.patch.object(al, "load_local_pause", return_value=False), \
         mock.patch.object(al, "drain_loop_commands", return_value=[]), \
         mock.patch.object(al, "load_send_requests", return_value=[]), \
         mock.patch.object(al, "_capture_pane", return_value="> "), \
         mock.patch.object(al.time, "time", return_value=100):
        scheduler._run_loop()


class GitLabIssueHookTests(unittest.TestCase):
    def test_update_advances_only_after_ack(self):
        issues = [
            {"iid": 1, "updated_at": "2026-08-02T00:00:01Z", "labels": []},
            {"iid": 2, "updated_at": "2026-08-02T00:00:02Z", "labels": []},
        ]
        cfg = {"entry_id": "test-entry", "cwd": "/tmp/repo"}
        updated_issues = [
            issues,
            [issues[0], {**issues[1], "updated_at": "2026-08-03T00:00:03Z"}],
            [issues[0], {**issues[1], "updated_at": "2026-08-03T00:00:03Z"}],
            [{**issues[0], "updated_at": "2026-08-04T00:00:04Z"},
             {**issues[1], "updated_at": "2026-08-03T00:00:03Z"}],
        ]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(hook, "hook_state_dir", return_value=pathlib.Path(tmp)), \
             mock.patch.object(hook, "_fetch_issues", side_effect=[(i, "g/p") for i in updated_issues]):
            self.assertIsNone(hook.check(cfg))
            first = hook.check(cfg)
            retried = hook.check(cfg)
            hook.ack()
            second = hook.check(cfg)
        self.assertIn('"iid": 2', first)
        self.assertIn('"iid": 2', retried)
        self.assertIn('"iid": 1', second)


class EventHookDispatchTests(unittest.TestCase):
    def test_hooks_accepts_one_or_many_scripts(self):
        single = al.validate_entries([{"hooks": "one.py", "interval_minutes": 1}])[0]
        multiple = al.validate_entries([
            {"hooks": ["one.py", "two.py"], "interval_minutes": 1}
        ])[0]

        self.assertEqual(single["hooks"], ["one.py"])
        self.assertEqual(multiple["hooks"], ["one.py", "two.py"])

    def test_hook_config_is_preserved(self):
        entry = al.validate_entries([{
            "hooks": "one.py",
            "hook_config": {"labels": ["ready"]},
            "interval_minutes": 1,
        }])[0]

        self.assertEqual(entry["hook_config"], {"labels": ["ready"]})

    def test_bundled_hook_is_resolved_by_name(self):
        with mock.patch.object(sys, "argv", [str(HERE.parent / "agent-loop.py")]):
            resolved = al._resolve_hook_path("gitlab-issue-hook")

        self.assertEqual(resolved, HERE.parent / "hooks" / "gitlab-issue-hook.py")

    def test_multiple_hooks_each_dispatch_their_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = pathlib.Path(tmp, "one.py")
            two = pathlib.Path(tmp, "two.py")
            one.write_text('def check():\n    return "one"\n', encoding="utf-8")
            two.write_text('def check():\n    return "two"\n', encoding="utf-8")
            entry = {
                "id": "p1", "name": "many", "prompt": "", "hooks": [str(one), str(two)],
                "next_run_at": 0, "enabled": True, "fresh_context": False,
                "interval_minutes": 1, "scheduled": True,
            }
            scheduler = _scheduler([entry])
            scheduler._update_entry = mock.Mock()
            scheduler._next_run_at_for_entry = mock.Mock(return_value=60)

            scheduler._fire_due_schedules(1)

        self.assertEqual([req["prompt"] for req in scheduler._pending], ["one", "two"])
        self.assertTrue(all(req["source"] == "hook" for req in scheduler._pending))

    def test_hooks_do_not_prewarm_llm_pane(self):
        mgr = al.SessionManager.__new__(al.SessionManager)
        mgr._panes = {}
        mgr._prompt_names = {}
        mgr._tmux_names = {}
        mgr._prompt_cwds = {}
        mgr._owners = {}
        mgr._lock = threading.Lock()
        mgr._start_pane = mock.Mock()
        mgr.write_state = mock.Mock()
        mgr.sync_entries([{"id": "control", "name": "control", "hooks": ["control.py"]}])
        mgr._start_pane.assert_not_called()

    def test_successful_event_dispatch_preserves_fresh_context_and_acks(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "hooks": ["hook.py"],
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": True, "fresh_context_interval_minutes": None,
                 "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        dispatched = scheduler._dispatch_prompt.call_args.args[0]
        self.assertEqual(dispatched["prompt"], "prompt")
        self.assertTrue(dispatched["_should_clear"])
        scheduler._call_hook_ack.assert_called_once_with(entry, "hook.py")

    def test_failed_event_dispatch_does_not_ack(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "hooks": ["hook.py"],
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": False, "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=False)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        scheduler._call_hook_ack.assert_not_called()

    def test_slot_rejection_does_not_ack(self):
        entry = {"id": "p1", "name": "issues", "prompt": "", "hooks": ["hook.py"],
                 "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
                 "fresh_context": False, "interval_minutes": 1, "scheduled": True}
        scheduler = _scheduler([entry])
        scheduler._semaphore = object()
        scheduler._call_hook_check = mock.Mock(return_value={"prompt": "prompt"})
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._try_acquire_slot = mock.Mock(return_value="defer")
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        scheduler._dispatch_prompt.assert_not_called()
        scheduler._call_hook_ack.assert_not_called()
        # defer → pending に戻る
        self.assertEqual(len(scheduler._pending), 1)

    def test_same_name_entries_keep_their_own_event_prompt(self):
        entries = [
            {"id": "p1", "name": "issues", "prompt": "", "hooks": ["one.py"],
             "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
             "fresh_context": False, "interval_minutes": 1, "scheduled": True},
            {"id": "p2", "name": "issues", "prompt": "", "hooks": ["two.py"],
             "next_run_at": 0, "enabled": True, "exclude_from_concurrency": False,
             "fresh_context": False, "interval_minutes": 1, "scheduled": True},
        ]
        scheduler = _scheduler(entries)
        scheduler._call_hook_check = mock.Mock(
            side_effect=lambda entry, _hook: {"prompt": entry["id"] + "-prompt"}
        )
        scheduler._call_hook_ack = mock.Mock()
        scheduler._dispatch_prompt = mock.Mock(return_value=True)
        scheduler._run_preflight = mock.Mock(return_value=True)
        _run_once(scheduler)
        sent = [(call.args[0]["id"], call.args[0]["prompt"])
                for call in scheduler._dispatch_prompt.call_args_list]
        self.assertEqual(sent, [("p1", "p1-prompt"), ("p2", "p2-prompt")])

    def test_hook_without_ack_remains_supported(self):
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._load_hook_module = mock.Mock(return_value=types.SimpleNamespace(check=lambda: "x"))
        scheduler._call_hook_ack({"name": "legacy"}, "hook.py")

    def test_slot_or_send_failure_keeps_prompt_for_next_drain(self):
        entry = {"id": "p1", "name": "issues", "exclude_from_concurrency": False}
        session = types.SimpleNamespace(
            ensure_session=mock.Mock(return_value=True),
            get_pane_id=mock.Mock(return_value="%1"),
            send_prompt=mock.Mock(side_effect=[False, True]),
            restart_pane=mock.Mock(),
            _lock=al.threading.Lock(),
            _prompt_cwds={},
        )
        semaphore = types.SimpleNamespace(
            slot_timeout=60,
            max_concurrent=1,
            slot_elapsed=mock.Mock(return_value=None),
            cooldown_remaining=mock.Mock(return_value=0),
            acquire=mock.Mock(side_effect=[False, True, True]),
            release=mock.Mock(),
        )
        scheduler = al.PeriodicScheduler.__new__(al.PeriodicScheduler)
        scheduler._session_mgr = session
        scheduler._semaphore = semaphore
        scheduler._slot_monitor = None
        scheduler._entries = [entry]
        scheduler._external_queues = {"issues": collections.deque(["prompt"])}
        scheduler._lock = al.threading.Lock()
        scheduler._stop_event = al.threading.Event()
        scheduler._pending = []
        scheduler._debouncer = al.RequestDebouncer(window_seconds=0)
        scheduler._draining = False
        scheduler._run_state = "run"
        scheduler._hook_quarantine = set()
        scheduler._active_count = 0
        scheduler._active_ids = set()
        scheduler._inflight_ack_paths = set()
        scheduler._health = {}
        scheduler._input_recovery = False
        scheduler._preflight_cache = {}
        scheduler._hook_cache = {}
        scheduler._hook_cache_lock = al.threading.Lock()
        scheduler._run_preflight = mock.Mock(return_value=True)

        with mock.patch.object(al, "_capture_pane", return_value="> "):
            scheduler._drain_external_to_pending()
            scheduler._process_pending()
            self.assertEqual(len(scheduler._pending), 1)
            scheduler._process_pending()
            self.assertEqual(len(scheduler._pending), 1)
            scheduler._process_pending()
        self.assertEqual(session.send_prompt.call_count, 2)
        self.assertEqual(list(scheduler._external_queues["issues"]), [])
        self.assertEqual(scheduler._pending, [])


class ResourceControlHookTests(unittest.TestCase):
    def test_runs_headless_writer_without_dispatching_prompt(self):
        completed = types.SimpleNamespace(returncode=0, stdout="{}\n", stderr="")
        cfg = {"hook_config": {
            "agent_audit": "/tmp/agent-audit",
            "script": "/tmp/resource-control.js",
            "control_dir": "/tmp/control",
            "budget_dir": "/tmp/budget",
        }}
        with mock.patch.object(resource_hook.subprocess, "run", return_value=completed) as run:
            self.assertIsNone(resource_hook.check(cfg))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["/tmp/agent-audit", "--budget-dir", "/tmp/budget",
             "collect", "--source", "cli-quota"],
            ["node", str(pathlib.Path("/tmp/resource-control.js").resolve()),
             "--control-dir", "/tmp/control", "--budget-dir", "/tmp/budget"],
        ])

    def test_quota_collect_failure_does_not_stop_existing_resource_control(self):
        failed = types.SimpleNamespace(returncode=127, stdout="", stderr="agent-audit not found")
        completed = types.SimpleNamespace(returncode=0, stdout="{}\n", stderr="")
        cfg = {"hook_config": {"script": "/tmp/resource-control.js"}}
        with mock.patch.object(resource_hook.subprocess, "run",
                               side_effect=[failed, completed]) as run:
            self.assertIsNone(resource_hook.check(cfg))
        self.assertEqual(run.call_count, 2)

    def test_collects_then_writes_calibration_without_prompt(self):
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        cfg = {"hook_config": {
            "agent_audit": "/tmp/agent-audit",
            "audit_dir": "/tmp/audit",
            "budget_dir": "/tmp/budget",
        }}
        with mock.patch.object(calibrate_hook.subprocess, "run", return_value=completed) as run:
            self.assertIsNone(calibrate_hook.check(cfg))
        base = ["/tmp/agent-audit", "--audit-dir", "/tmp/audit", "--budget-dir", "/tmp/budget"]
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            base + ["collect"], base + ["calibrate", "--write"],
            base + ["extract"], base + ["distill", "--review"],
            base + ["tune", "--apply"],
        ])


class MemoryMaintenanceHookTests(unittest.TestCase):
    """LLM を使わない記憶メンテナンス（計画 K1）。削除は絶対に走らせない。"""

    def _skill_home(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="agent-loop-skills-"))
        for skill, names in (("ltm-use", ("build_index.py", "review_memory.py",
                                          "cleanup_memory.py")),
                             ("wiki-use", ("wiki_lint.py",))):
            scripts = root / skill / "scripts"
            scripts.mkdir(parents=True)
            for name in names:
                (scripts / name).write_text("", encoding="utf-8")
        return root

    def test_runs_non_destructive_maintenance_then_collects_without_prompt(self):
        root = self._skill_home()
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        cfg = {"hook_config": {"skill_home": str(root), "python": "python3",
                               "agent_audit": "/tmp/agent-audit", "audit_dir": "/tmp/audit"}}
        with mock.patch.object(memory_hook.subprocess, "run", return_value=completed) as run:
            self.assertIsNone(memory_hook.check(cfg))
        calls = [c.args[0] for c in run.call_args_list]
        ltm = root / "ltm-use" / "scripts"
        self.assertEqual(calls, [
            ["python3", str(ltm / "build_index.py"), "--scope", "home", "--force"],
            ["python3", str(ltm / "review_memory.py"), "--scope", "home", "--update-retention"],
            ["python3", str(root / "wiki-use" / "scripts" / "wiki_lint.py")],
            ["/tmp/agent-audit", "--audit-dir", "/tmp/audit",
             "collect", "--source", "memory-store"],
        ])

    def test_never_runs_deletion(self):
        root = self._skill_home()
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(memory_hook.subprocess, "run", return_value=completed) as run:
            memory_hook.check({"hook_config": {"skill_home": str(root)}})
        flat = " ".join(" ".join(c.args[0]) for c in run.call_args_list)
        self.assertNotIn("cleanup_memory.py", flat, "削除は承認を経た当番だけが実行する")
        self.assertNotIn("--fix", flat, "wiki lint は報告のみ")

    def test_missing_skills_still_collect_and_failures_are_reported(self):
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(memory_hook.subprocess, "run", return_value=completed) as run:
            memory_hook.check({"hook_config": {}})
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["agent-audit", "collect", "--source", "memory-store"]])

        root = self._skill_home()
        failed = types.SimpleNamespace(returncode=1, stdout="", stderr="index broken")
        with mock.patch.object(memory_hook.subprocess, "run",
                               side_effect=[failed, completed, completed, completed]) as run:
            with self.assertRaises(RuntimeError) as caught:
                memory_hook.check({"hook_config": {"skill_home": str(root)}})
        self.assertIn("build_index.py", str(caught.exception))
        self.assertEqual(run.call_count, 4, "1 つ失敗しても残りの整理は回す")


if __name__ == "__main__":
    unittest.main()
