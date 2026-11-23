"""セッション側フェッチャの CLI を一時フォルダで確かめる。"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROGRAM = Path(__file__).resolve().parents[1] / "session_fetcher.py"
spec = importlib.util.spec_from_file_location("session_fetcher", PROGRAM)
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)

NAME = "request-123.json"
BODY = '{"text": "テストの依頼本文"}\n'.encode("utf-8")


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fetcher.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SessionFetcherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source"
        self.dest = root / "dest"
        self.state = root / "state" / "fetched.json"
        self.heartbeat = root / "state" / "heartbeat.json"
        self.source.mkdir()
        self.old_mtime = int(time.time()) - 60

    def write_source(self, name=NAME, body=BODY, *, mtime=None):
        path = self.source / name
        path.write_bytes(body)
        stamp = self.old_mtime if mtime is None else mtime
        os.utime(path, (stamp, stamp))
        return path

    def fetch(self, *, heartbeat=False, settle_seconds=10):
        args = ["fetch-once", "--source", str(self.source), "--dest", str(self.dest),
                "--state", str(self.state), "--settle-seconds", str(settle_seconds)]
        if heartbeat:
            args.extend(["--heartbeat", str(self.heartbeat)])
        return run_main(*args)

    def fetch_ok(self, *, heartbeat=False):
        code, out, err = self.fetch(heartbeat=heartbeat)
        self.assertEqual(code, 0, (out, err))
        return json.loads(out)

    def test_fetches_new_file(self):
        source = self.write_source()
        self.assertEqual(self.fetch_ok(), {
            "fetched": [NAME], "skipped_settling": [], "already": 0, "errors": [],
        })
        self.assertEqual((self.dest / NAME).read_bytes(), BODY)
        self.assertEqual(source.read_bytes(), BODY)
        self.assertEqual(source.stat().st_mtime, self.old_mtime)

    def test_already_fetched_file_is_not_replaced(self):
        self.write_source()
        self.fetch_ok()
        copied = self.dest / NAME
        os.utime(copied, (self.old_mtime, self.old_mtime))
        self.assertEqual(self.fetch_ok(), {
            "fetched": [], "skipped_settling": [], "already": 1, "errors": [],
        })
        self.assertEqual(copied.stat().st_mtime, self.old_mtime)

    def test_waits_for_file_to_settle(self):
        source = self.write_source(mtime=time.time())
        self.assertEqual(self.fetch_ok()["skipped_settling"], [NAME])
        self.assertFalse((self.dest / NAME).exists())
        os.utime(source, (self.old_mtime, self.old_mtime))
        self.assertEqual(self.fetch_ok()["fetched"], [NAME])

    def test_saves_readable_ledger_and_stops_on_corruption(self):
        self.write_source()
        self.fetch_ok()
        entry = json.loads(self.state.read_text(encoding="utf-8"))["files"][NAME]
        self.assertEqual(entry["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(entry["size"], len(BODY))
        self.assertEqual(datetime.fromisoformat(entry["fetched_at"]).tzinfo, timezone.utc)
        self.assertEqual(datetime.fromisoformat(entry["source_mtime"]),
                         datetime.fromtimestamp(self.old_mtime, timezone.utc))
        self.assertEqual(self.fetch_ok()["already"], 1)
        self.write_source(body=b"{}")
        for invalid in ("{", "[]"):
            with self.subTest(invalid=invalid):
                self.state.write_text(invalid, encoding="utf-8")
                code, _, err = self.fetch(heartbeat=True)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(self.state.read_text(encoding="utf-8"), invalid)
                self.assertEqual((self.dest / NAME).read_bytes(), BODY)
                self.assertFalse(self.heartbeat.exists())

    def test_updates_heartbeat(self):
        self.write_source()
        self.write_source("settling.json", mtime=time.time())
        self.fetch_ok(heartbeat=True)
        heartbeat = json.loads(self.heartbeat.read_text(encoding="utf-8"))
        self.assertEqual(datetime.fromisoformat(heartbeat.pop("last_run")).tzinfo, timezone.utc)
        self.assertEqual(heartbeat, {"fetched": 1, "skipped_settling": 1, "errors": 0})

    def test_rejects_state_inside_destination(self):
        self.state = self.dest / "fetched.json"
        code, _, err = self.fetch()
        self.assertEqual(code, 2)
        self.assertIn("出力先は受信フォルダの外に指定してください", err)
        self.assertFalse(self.dest.exists())

    def test_rejects_heartbeat_inside_destination(self):
        self.heartbeat = self.dest / "status" / "heartbeat.json"
        code, _, err = self.fetch(heartbeat=True)
        self.assertEqual(code, 2)
        self.assertIn("出力先は受信フォルダの外に指定してください", err)
        self.assertFalse(self.dest.exists())

    def test_rejects_negative_settle_seconds(self):
        code, _, err = self.fetch(settle_seconds=-1)
        self.assertEqual(code, 2)
        self.assertIn("--settle-seconds に負数は指定できません", err)
        self.assertFalse(self.dest.exists())

    def test_missing_source_does_not_write_heartbeat(self):
        self.source.rmdir()
        code, _, err = self.fetch(heartbeat=True)
        self.assertEqual(code, 2)
        self.assertTrue(err)
        self.assertFalse(self.heartbeat.exists())

    def test_recopies_changed_source(self):
        self.write_source()
        self.fetch_ok()
        changed = BODY.replace(b"text", b"body")
        self.write_source(body=changed)
        self.assertEqual(self.fetch_ok()["fetched"], [NAME])
        self.assertEqual((self.dest / NAME).read_bytes(), changed)

    def test_recopies_missing_or_changed_destination(self):
        self.write_source()
        self.fetch_ok()
        copied = self.dest / NAME
        copied.unlink()
        self.assertEqual(self.fetch_ok()["fetched"], [NAME])
        copied.write_bytes(b"{}")
        self.assertEqual(self.fetch_ok()["fetched"], [NAME])
        self.assertEqual(copied.read_bytes(), BODY)

    def test_rebuilds_missing_ledger(self):
        self.write_source()
        self.fetch_ok()
        self.state.unlink()
        self.assertEqual(self.fetch_ok()["fetched"], [NAME])
        self.assertEqual((self.dest / NAME).read_bytes(), BODY)
        entry = json.loads(self.state.read_text(encoding="utf-8"))["files"][NAME]
        self.assertEqual(entry["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(self.fetch_ok()["already"], 1)

    def test_only_copies_visible_top_level_json_in_name_order(self):
        for name in ("b.json", ".hidden.json", "request.txt", "a.json"):
            self.write_source(name)
        nested = self.source / "sub.json"
        nested.mkdir()
        (nested / "nested.json").write_bytes(BODY)
        self.assertEqual(self.fetch_ok()["fetched"], ["a.json", "b.json"])
        self.assertEqual(sorted(path.name for path in self.dest.iterdir()), ["a.json", "b.json"])

    def test_copy_failure_is_reported_and_remaining_files_are_fetched(self):
        self.write_source("a.json")
        self.write_source("b.json")
        replace = os.replace

        def fail_one_copy(source, dest):
            if Path(dest) == self.dest / "a.json":
                raise PermissionError("置き換えに失敗")
            replace(source, dest)

        with patch.object(fetcher.os, "replace", side_effect=fail_one_copy):
            code, out, err = self.fetch(heartbeat=True)
        self.assertEqual(code, 2, err)
        result = json.loads(out)
        self.assertEqual(result["fetched"], ["b.json"])
        self.assertEqual([error["file"] for error in result["errors"]], ["a.json"])
        self.assertIn("置き換えに失敗", result["errors"][0]["error"])
        self.assertEqual(list(self.dest.iterdir()), [self.dest / "b.json"])
        heartbeat = json.loads(self.heartbeat.read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["errors"], 1)
        self.assertEqual(self.fetch_ok()["fetched"], ["a.json"])


if __name__ == "__main__":
    unittest.main()
