"""フェイクの履歴コマンドと GitLab API で集約を検証する。"""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dagu_failure_digest as digest


class DigestTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / "config.json"
        self.state_path = self.root / "state.json"
        self.now = datetime.now(timezone.utc)
        (self.root / "history.py").write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "name = sys.argv[1] if len(sys.argv) > 1 else 'all'\n"
            "if name == 'broken':\n"
            "    sys.exit(7)\n"
            "if name == 'slow':\n"
            "    import time\n"
            "    time.sleep(5)\n"
            "print(Path(name + '.json').read_text(encoding='utf-8'))\n",
            encoding="utf-8",
        )
        self.config = {
            "history_command": [sys.executable, "history.py", "{dag}"],
            "dags": ["nightly"],
            "state_file": "state.json",
        }
        self.enter_patch(patch.dict(os.environ, {
            "GITLAB_HUB_ISSUE_URL": "https://gitlab.example/group/sub/project/-/issues/15",
            "GITLAB_TOKEN": "test-token",
            "DAGU_FAILURE_DIGEST_DRY_RUN": "",
        }))
        self.opener = self.enter_patch(patch.object(digest, "build_opener"))
        self.response = self.opener.return_value.open.return_value.__enter__.return_value
        self.response.status = 201

    def enter_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def at(self, hours):
        return (self.now - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")

    def record(self, run_id, status="failed", hours=1):
        return {
            "dagRunId": run_id,
            "status": status,
            "startedAt": self.at(hours + 0.25),
            "finishedAt": self.at(hours),
            "nodes": [{"name": "収集", "status": "Error"}, {"name": "準備", "status": "succeeded"}],
        }

    def history(self, rows, source="nightly"):
        (self.root / f"{source}.json").write_text(json.dumps(rows), encoding="utf-8")

    def state(self, watermark, ids, source="nightly"):
        self.state_path.write_text(json.dumps({
            "version": 1, "sources": {source: {"watermark": watermark, "reported_ids": ids}},
        }), encoding="utf-8")

    def invoke(self, *extra):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = digest.main(["--config", str(self.config_path), "--date", "2026-11-21", *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_failure_status_and_steps(self):
        selected = ["FAILED", "error", "Partially_Succeeded", "partial_success"]
        ignored = ["succeeded", "running", "queued", "cancelled"]
        self.history([self.record(status, status) for status in selected + ignored])
        code, body, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(body.startswith("<!-- dagu-failure-digest 2026-11-21 -->\n"))
        for status in selected:
            self.assertIn(f"実行 ID: {digest.markdown(status)}", body)
        for status in ignored:
            self.assertNotIn(f"実行 ID: {status}", body)
        self.assertIn("失敗ステップ: 収集", body)
        self.assertNotIn("準備", body)

    def test_watermark_and_reported_ids(self):
        self.state(self.at(2), ["duplicate"])
        self.history([self.record("old", hours=3), self.record("boundary", hours=2),
                      self.record("duplicate"), self.record("new")])
        code, body, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("実行 ID: new", body)
        self.assertEqual(body.count("実行 ID:"), 1)

    def test_dry_run_preserves_pending(self):
        self.config["dry_run"] = False
        self.history([self.record("new")])
        with patch.dict(os.environ, {"DAGU_FAILURE_DIGEST_DRY_RUN": "1"}):
            first = self.invoke()
        with patch.dict(os.environ, {"DAGU_FAILURE_DIGEST_DRY_RUN": "0"}):
            second = self.invoke("--dry-run")
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        self.assertIn("実行 ID: new", first[1])
        self.assertIn("dry-run", first[2])
        self.opener.assert_not_called()
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.state_path.with_suffix(".json.lock").exists())

    def test_post_and_second_run(self):
        self.history([self.record("older", hours=2), self.record("new"),
                      self.record("latest", "succeeded", hours=0.5)])
        with patch.dict(os.environ, {"DAGU_FAILURE_DIGEST_DRY_RUN": "0"}):
            code, body, _ = self.invoke()
            second = self.invoke()
        self.assertEqual(code, 0)
        self.opener.assert_called_once()
        self.assertIsInstance(self.opener.call_args.args[0], digest.NoRedirect)
        request = self.opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url,
                         "https://gitlab.example/api/v4/projects/group%2Fsub%2Fproject/issues/15/notes")
        self.assertEqual(request.get_header("Private-token"), "test-token")
        self.assertEqual(json.loads(request.data), {"body": body})
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["sources"]["nightly"], {
            "watermark": digest.parse_time(self.at(0.5)).isoformat(),
            "reported_ids": ["new", "older"],
        })
        self.assertEqual(second[0], 0)
        self.assertIn("✅ Dagu の実行失敗はありません", second[1])
        self.assertIn("投稿対象がない", second[2])

    def test_all_sources_fail(self):
        (self.root / "invalid.json").write_text("invalid JSON", encoding="utf-8")
        for command, source, reason in (
            ([sys.executable, "history.py", "{dag}"], "broken", "終了コード 7"),
            (["./missing"], "nightly", "見つかりません"),
            ([sys.executable, "history.py", "{dag}"], "invalid", "JSON を読み取れません"),
            ([sys.executable, "history.py", "{dag}"], "slow", "タイムアウト"),
        ):
            with self.subTest(source=source, reason=reason):
                self.config.update(history_command=command, dags=[source],
                                   dry_run=False, timeout_seconds=0.2)
                code, body, _ = self.invoke()
                self.assertEqual(code, 3)
                self.assertIn("未確認（履歴を取得できませんでした）", body)
                self.assertIn(reason, body)
                self.assertNotIn("実行失敗はありません", body)
                self.assertFalse(self.state_path.exists())
        self.assertEqual(self.opener.call_count, 4)

    def test_partial_failure_preserves_failed_source(self):
        self.config.update(dags=["nightly", "broken"], dry_run=False)
        self.state(self.at(3), ["previous"], "broken")
        old = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.history({"dagRuns": [self.record("new")]})
        code, body, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("nightly —", body)
        self.assertIn("実行 ID: new", body)
        self.assertIn("broken — 履歴取得コマンド失敗", body)
        sources = json.loads(self.state_path.read_text(encoding="utf-8"))["sources"]
        self.assertEqual(sources["broken"], old["sources"]["broken"])
        self.assertEqual(sources["nightly"]["reported_ids"], ["new"])

    def test_initial_lookback_uses_finish_time(self):
        self.config["lookback_hours"] = 2
        recent = self.record("new")
        recent["startedAt"] = self.at(10)
        self.history({"history": [self.record("old", hours=3), recent]})
        code, body, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("実行 ID: new", body)
        self.assertNotIn("実行 ID: old", body)

    def test_single_command_and_field_aliases(self):
        self.config.update(history_command=[sys.executable, "history.py"], dry_run=False)
        del self.config["dags"]
        self.history({"items": [
            {"dag": "backup", "runId": "abc", "status": "failed", "started_at": self.at(1),
             "steps": [{"name": "失敗\n<step>", "status": "partial_success"}]},
            {"dagName": "deploy", "id": "def", "status": "error", "updatedAt": self.at(2)},
            {"dagName": "skip", "status": "failed", "createdAt": self.at(1)},
        ]}, "all")
        code, body, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("backup —", body)
        self.assertIn("失敗 &#60;step&#62;", body)
        self.assertIn("deploy — 開始時刻不明 — 失敗ステップ: ステップ不明", body)
        self.assertNotIn("skip", body)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["sources"]["__all__"]["reported_ids"], ["abc", "def"])

    def test_failed_post_leaves_state_untouched(self):
        self.config["dry_run"] = False
        self.state(self.at(2), ["previous"])
        old = self.state_path.read_bytes()
        self.history([self.record("new")])
        self.response.status = 302
        code, body, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("実行 ID: new", body)
        self.assertIn("HTTP 302", stderr)
        self.assertEqual(self.state_path.read_bytes(), old)

    def test_corrupt_state_is_initial(self):
        self.state_path.write_text('{"version": 1, "sources": {"nightly": {}}}', encoding="utf-8")
        self.history([self.record("new")])
        code, body, stderr = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("実行 ID: new", body)
        self.assertIn("状態ファイルが壊れている", stderr)

    def test_invalid_config(self):
        for updates in (
            {"dags": []}, {"dry_run": "true"}, {"history_command": "dagu"},
            {"timeout_seconds": True}, {"lookback_hours": float("inf")}, {"state_file": 0},
        ):
            with self.subTest(updates=updates), patch.dict(self.config, updates):
                self.assertEqual(self.invoke()[0], 2)
        self.opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
