import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402


class StaleSlotCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.slots_dir = Path(self._tmpdir.name) / "slots"
        self.slots_dir.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_slot(self, name: str, pid: int, acquired_at: float) -> Path:
        path = self.slots_dir / name
        path.write_text(json.dumps({
            "pane_id": name.replace("pane_", "").replace(".json", ""),
            "pid": pid,
            "acquired_at": acquired_at,
        }), encoding="utf-8")
        return path

    def _write_cooldown(self, name: str, released_at: float) -> Path:
        path = self.slots_dir / name
        path.write_text(json.dumps({"released_at": released_at}), encoding="utf-8")
        return path

    def test_alive_pid_kept_past_timeout(self):
        alive_path = self._write_slot("pane_alive.json", os.getpid(), 0.0)
        stats = al.cleanup_stale_slots_on_startup(
            slot_timeout=60,
            slots_dir=self.slots_dir,
        )
        self.assertTrue(alive_path.exists())
        self.assertEqual(stats["slots_kept_alive"], 1)
        self.assertEqual(stats["slots_deleted"], 0)

    def test_dead_pid_deleted(self):
        dead_path = self._write_slot("pane_dead.json", 999999, al.time.time())
        stats = al.cleanup_stale_slots_on_startup(slots_dir=self.slots_dir)
        self.assertFalse(dead_path.exists())
        self.assertEqual(stats["slots_deleted"], 1)

    def test_corrupt_slot_deleted(self):
        corrupt = self.slots_dir / "pane_bad.json"
        corrupt.write_text("{not json", encoding="utf-8")
        stats = al.cleanup_stale_slots_on_startup(slots_dir=self.slots_dir)
        self.assertFalse(corrupt.exists())
        self.assertEqual(stats["slots_deleted"], 1)

    def test_expired_no_pid_deleted(self):
        expired = self._write_slot("pane_old.json", 0, 0.0)
        stats = al.cleanup_stale_slots_on_startup(
            slot_timeout=60,
            slots_dir=self.slots_dir,
        )
        self.assertFalse(expired.exists())
        self.assertEqual(stats["slots_deleted"], 1)

    def test_cooldown_expired_deleted(self):
        old = self._write_cooldown("cooldown_old.json", 0.0)
        recent = self._write_cooldown("cooldown_new.json", al.time.time())
        stats = al.cleanup_stale_slots_on_startup(
            cooldown_seconds=30,
            slots_dir=self.slots_dir,
        )
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(stats["cooldowns_deleted"], 1)

    def test_count_active_slots_keeps_alive_past_timeout(self):
        self._write_slot("pane_alive.json", os.getpid(), 0.0)
        sem = al.GlobalSemaphore(max_concurrent=3, slot_timeout_seconds=60)
        with mock.patch.object(al, "_SLOTS_DIR", self.slots_dir):
            count = sem._count_active_slots()
        self.assertEqual(count, 1)
        self.assertTrue((self.slots_dir / "pane_alive.json").exists())


class SlotMonitorFreezeTests(unittest.TestCase):
    def test_freeze_callback_on_unchanged_hash(self):
        sem = al.GlobalSemaphore(0)
        called: list[str] = []

        def on_freeze(pane_id: str) -> None:
            called.append(pane_id)

        monitor = al.SlotMonitor(sem, freeze_timeout_seconds=1, on_freeze=on_freeze)
        monitor.track("%1")
        with monitor._lock:
            monitor._pending["%1"]["state"] = "processing"
            monitor._pending["%1"]["content_hash"] = "abc"
            monitor._pending["%1"]["hash_unchanged_since"] = al.time.time() - 5

        with (
            mock.patch.object(al.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="%0")),
            mock.patch.object(al, "_capture_pane", return_value="same content"),
            mock.patch.object(al._CLI_PROFILE, "is_idle", return_value=False),
        ):
            digest = mock.Mock()
            digest.hexdigest.return_value = "abc"
            with mock.patch.object(al.hashlib, "sha256", return_value=digest):
                monitor._check_pane("%1")

        self.assertEqual(called, ["%1"])

    def test_an_unset_config_takes_the_limit_from_the_definition(self):
        """未設定は「止めない」ではない（設計 2026-08-27 §7.4-3 / 実装計画 段 11）。

        ヘッドレスは同じ意味の無進捗上限を既定で持っている（定義の `timeout`、無ければ
        共通 fallback の 600 秒）のに、ペインだけ既定 off で `slot_timeout_seconds`
        （既定 7200）まで居座っていた。**同じ性質の上限は同じ源から採る**。
        """
        monitor = al.SlotMonitor(al.GlobalSemaphore(0))
        self.assertIsNone(monitor._freeze_timeout, "書いていないことを覚えておく")
        declared = al.CliProfile("x", {"command": ["x"], "timeout": 42,
                                       "interactive": {"command": ["x"]}})
        self.assertEqual(monitor._freeze_limit(declared), 42.0)
        bare = al.CliProfile("x", {"command": ["x"], "interactive": {"command": ["x"]}})
        self.assertEqual(monitor._freeze_limit(bare),
                         float(al._harness_toolloop._TL_DEFAULT_AGENT_TIMEOUT_SEC),
                         "宣言が無ければヘッドレスと同じ共通 fallback")

    def test_an_explicit_zero_still_means_do_not_stop(self):
        """`0` と書いた人の意図（止めない）は守る。未設定と同じにしない。"""
        monitor = al.SlotMonitor(al.GlobalSemaphore(0), freeze_timeout_seconds=0)
        declared = al.CliProfile("x", {"command": ["x"], "timeout": 42,
                                       "interactive": {"command": ["x"]}})
        self.assertEqual(monitor._freeze_limit(declared), 0.0)

    def test_an_explicit_value_wins_over_the_definition(self):
        monitor = al.SlotMonitor(al.GlobalSemaphore(0), freeze_timeout_seconds=5)
        declared = al.CliProfile("x", {"command": ["x"], "timeout": 42,
                                       "interactive": {"command": ["x"]}})
        self.assertEqual(monitor._freeze_limit(declared), 5.0)

    def test_a_stuck_pane_is_caught_without_waiting_for_the_slot_timeout(self):
        """受入条件。止まったペインが `slot_timeout_seconds` を待たずに検知される。"""
        called: list[str] = []
        # スロット上限は 2 時間、定義の無進捗上限は 1 秒。設定は書かない（未設定）。
        monitor = al.SlotMonitor(al.GlobalSemaphore(0), slot_timeout_seconds=7200,
                                 on_freeze=lambda p: called.append(p))
        profile = al.CliProfile("x", {"command": ["x"], "timeout": 1,
                                      "interactive": {"command": ["x"]}})
        monitor.track("%1", profile=profile)
        with monitor._lock:
            monitor._pending["%1"].update(state="processing", content_hash="abc",
                                          hash_unchanged_since=al.time.time() - 5)
        with mock.patch.object(al.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="%0")), \
                mock.patch.object(al, "_capture_pane", return_value="止まった画面"), \
                mock.patch.object(type(profile), "is_idle", lambda *_a, **_k: False):
            digest = mock.Mock()
            digest.hexdigest.return_value = "abc"
            with mock.patch.object(al.hashlib, "sha256", return_value=digest):
                monitor._check_pane("%1")
        self.assertEqual(called, ["%1"])

    def test_ready_pane_not_freeze_checked(self):
        sem = al.GlobalSemaphore(0)
        called: list[str] = []
        monitor = al.SlotMonitor(sem, freeze_timeout_seconds=1, on_freeze=lambda p: called.append(p))
        monitor.track("%1")
        with monitor._lock:
            monitor._pending["%1"]["state"] = "waiting_start"

        with (
            mock.patch.object(al.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="%0")),
            mock.patch.object(al, "_capture_pane", return_value="> "),
            mock.patch.object(al._CLI_PROFILE, "is_idle", return_value=True),
        ):
            monitor._check_pane("%1")

        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
