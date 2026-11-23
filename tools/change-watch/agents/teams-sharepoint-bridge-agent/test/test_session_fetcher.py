"""セッション側フェッチャの CLI を一時フォルダで確かめる。"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.source.mkdir()

    def write_source(self, name=NAME):
        (self.source / name).write_bytes(BODY)

    def fetch(self):
        return run_main("fetch-once", "--source", str(self.source), "--dest", str(self.dest))

    def fetch_ok(self):
        code, out, err = self.fetch()
        self.assertEqual(code, 0, (out, err))
        return json.loads(out)

    def test_copies_new_file(self):
        self.write_source()
        self.assertEqual(self.fetch_ok(), {"copied": [NAME], "errors": []})
        self.assertEqual((self.dest / NAME).read_bytes(), BODY)

    def test_replaces_temporary_file_with_final_name(self):
        self.write_source()
        replace = os.replace

        def check_temporary_file(source, dest):
            tmp = Path(source)
            self.assertEqual(tmp.parent, self.dest)
            self.assertTrue(tmp.name.startswith(".tmp-"))
            self.assertFalse(tmp.match("*.json"))
            self.assertEqual(tmp.read_bytes(), BODY)
            self.assertEqual(Path(dest), self.dest / NAME)
            self.assertFalse(Path(dest).exists())
            replace(source, dest)

        with patch.object(fetcher.os, "replace", side_effect=check_temporary_file):
            self.fetch_ok()
        self.assertEqual(list(self.dest.iterdir()), [self.dest / NAME])

    def test_leaves_destination_only_file_in_place(self):
        self.write_source()
        self.dest.mkdir()
        existing = self.dest / "existing.json"
        existing.write_bytes(b"{}")
        before = existing.stat().st_mtime_ns
        self.assertEqual(self.fetch_ok()["copied"], [NAME])
        self.assertEqual(existing.read_bytes(), b"{}")
        self.assertEqual(existing.stat().st_mtime_ns, before)

    def test_copies_again_on_second_run(self):
        self.write_source()
        for _ in range(2):
            self.assertEqual(self.fetch_ok()["copied"], [NAME])
            self.assertEqual((self.dest / NAME).read_bytes(), BODY)

    def test_missing_source(self):
        self.source.rmdir()
        code, _, err = self.fetch()
        self.assertEqual(code, 2)
        self.assertIn("同期フォルダがありません", err)
        self.assertFalse(self.dest.exists())

    def test_rejects_destination_inside_source(self):
        for dest in (self.source, self.source / "inbox"):
            with self.subTest(dest=dest):
                self.dest = dest
                code, _, err = self.fetch()
                self.assertEqual(code, 2)
                self.assertIn("出力先は同期フォルダの外に指定してください", err)
        self.assertFalse(self.dest.exists())

    def test_only_copies_visible_top_level_json_in_name_order(self):
        for name in ("b.json", ".hidden.json", "request.txt", "a.json"):
            self.write_source(name)
        nested = self.source / "sub.json"
        nested.mkdir()
        (nested / "nested.json").write_bytes(BODY)
        self.assertEqual(self.fetch_ok()["copied"], ["a.json", "b.json"])
        self.assertEqual(sorted(path.name for path in self.dest.iterdir()), ["a.json", "b.json"])

    def test_copy_failure_is_reported_and_remaining_files_are_copied(self):
        self.write_source("a.json")
        self.write_source("b.json")
        replace = os.replace

        def fail_one_copy(source, dest):
            if Path(dest) == self.dest / "a.json":
                raise PermissionError("置き換えに失敗")
            replace(source, dest)

        with patch.object(fetcher.os, "replace", side_effect=fail_one_copy):
            code, out, err = self.fetch()
        self.assertEqual(code, 2, err)
        result = json.loads(out)
        self.assertEqual(result["copied"], ["b.json"])
        self.assertEqual([error["file"] for error in result["errors"]], ["a.json"])
        self.assertIn("置き換えに失敗", result["errors"][0]["error"])
        self.assertEqual(list(self.dest.iterdir()), [self.dest / "b.json"])


if __name__ == "__main__":
    unittest.main()
