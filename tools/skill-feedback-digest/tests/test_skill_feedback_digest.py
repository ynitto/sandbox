from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_feedback_digest as digest

FIXTURE = Path(__file__).parent / "fixtures/registry.json"
DAY = date(2026, 1, 5)


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.skills = digest.summarize(digest.read_object(FIXTURE))
        self.by_name = {skill.name: skill for skill in self.skills}
        self.markdown = digest.render_digest(self.skills, DAY)

    def section(self, title):
        return self.markdown.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]

    def test_counts_latest_and_unrefined(self):
        unstable = self.by_name["unstable"]
        self.assertEqual(dict(unstable.counts), {"ok": 1, "needs-improvement": 1, "broken": 2})
        self.assertEqual(unstable.latest_at, "2026-01-04T02:00:00Z")
        self.assertEqual(unstable.unrefined, 1)
        self.assertEqual(unstable.threshold, 1)
        self.assertEqual(self.by_name["recovered"].unrefined, 0)
        self.assertEqual(self.by_name["streak"].threshold, 3)
        self.assertEqual(self.by_name["pending-only"].threshold, 5)
        self.assertIn("| unstable | 1 | 1 | 2 | 2026-01-04T02:00:00Z | 1 |", self.markdown)

    def test_sections(self):
        broken = self.section("broken の記録あり")
        self.assertIn("unstable", broken)
        self.assertIn("変換が停止 ログを確認", broken)
        self.assertIn("recovered", broken)
        self.assertNotIn("過去の停止", broken)
        pending = self.section("改良待ちまたはしきい値到達")
        self.assertIn("unstable", pending)
        self.assertIn("pending-only", pending)
        self.assertNotIn("streak", pending)
        streak = self.section("needs-improvement が連続")
        self.assertIn("**streak** — 直近 2 件連続", streak)
        self.assertNotIn("recovered", streak)
        unused = self.section("フィードバックなし（未使用候補）")
        self.assertIn("**unused** — 登録日時 2026-01-04T00:00:00Z", unused)
        self.assertNotIn("recovered", unused)

    def test_draft_text(self):
        self.assertEqual(self.markdown.splitlines()[0], "<!-- skill-feedback-digest:2026-01-05 -->")
        draft = self.section("改良提案リスト（下書き）")
        self.assertEqual(draft.count("**unstable**"), 1)
        self.assertIn("broken（変換が停止 ログを確認）。次の一手は git-skill-manager の refine", draft)
        self.assertIn("needs-improvement（設定例が不足）、needs-improvement（手順が長い）", draft)
        self.assertIn("次の一手は skill-evaluator で現行版を評価", draft)
        self.assertIn("**pending-only**", draft)
        self.assertNotIn("unused", draft)
        self.assertEqual(self.markdown, digest.render_digest(self.skills, DAY))


class CliTest(unittest.TestCase):
    def run_cli(self, args):
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
            code = digest.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_missing_unreadable_and_invalid_registry_skip_posting(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.json"
            config.write_text('{"dry_run": false}', encoding="utf-8")
            invalid = base / "invalid.json"
            invalid.write_bytes(b"\xff")
            with patch.object(digest, "post_digest") as post:
                for registry in (base / "missing.json", base, invalid):
                    with self.subTest(registry=registry):
                        code, out, _ = self.run_cli(["--config", str(config), "--registry", str(registry)])
                        self.assertEqual(code, 0)
                        self.assertTrue(out.startswith("未確認（"))
                post.assert_not_called()

    def test_default_dry_run_does_not_post_or_change_registry(self):
        before = FIXTURE.read_bytes()
        with patch.object(digest, "post_digest") as post:
            code, out, err = self.run_cli(["--registry", str(FIXTURE)])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("<!-- skill-feedback-digest:"))
        self.assertIn("dry-run", err)
        self.assertEqual(FIXTURE.read_bytes(), before)
        post.assert_not_called()

    def test_path_priority_and_explicit_posting(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"registry_path": str(FIXTURE), "dry_run": False}), encoding="utf-8")
            with patch.dict(os.environ, {
                "SKILL_FEEDBACK_REGISTRY_PATH": str(Path(directory) / "missing.json"),
                "GITLAB_HUB_ISSUE_URL": "https://gitlab.example/org/hub/-/issues/42",
            }), patch.object(digest, "post_digest", return_value=True) as post:
                args = ["--config", str(config)]
                self.assertIn("未確認", self.run_cli(args)[1])
                post.assert_not_called()
                args += ["--registry", str(FIXTURE)]
                self.assertEqual(self.run_cli(args + ["--dry-run"])[0], 0)
                post.assert_not_called()
                code, out, err = self.run_cli(args)
                self.assertEqual(code, 0)
                self.assertIn("投稿しました", err)
                post.assert_called_once_with(out, os.environ["GITLAB_HUB_ISSUE_URL"])

    def test_config_path_and_default_use_existing_registry_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.json"
            config.write_text(json.dumps({"registry_path": str(FIXTURE)}), encoding="utf-8")
            with patch.dict(os.environ, {"USERPROFILE": directory}, clear=True):
                self.assertEqual(Path(digest.default_registry_path()), base / ".copilot/skill-registry.json")
                self.assertTrue(self.run_cli(["--config", str(config)])[1].startswith("<!--"))


class PostingTest(unittest.TestCase):
    marker = "<!-- skill-feedback-digest:2026-01-05 -->"
    issue_url = "https://gitlab.example/git/org/team/hub/-/issues/42"

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "GITLAB_URL": "https://gitlab.example/git",
            "GITLAB_TOKEN": "fake-token",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def response(self, notes, next_page=""):
        response = BytesIO(json.dumps(notes).encode())
        response.headers = {"X-Next-Page": next_page}
        return response

    @patch.object(digest, "build_opener")
    def test_same_day_match_on_later_page_skips_post(self, opener):
        request = opener.return_value.open
        request.side_effect = [
            self.response([{"body": self.marker.replace("01-05", "01-04")}], "2"),
            self.response([{"body": self.marker + "\r\n集計"}]),
        ]
        self.assertFalse(digest.post_digest(self.marker + "\n集計", self.issue_url))
        calls = [call.args[0] for call in request.call_args_list]
        self.assertEqual([req.get_method() for req in calls], ["GET", "GET"])
        self.assertTrue(calls[1].full_url.endswith("page=2"))

    @patch.object(digest, "build_opener")
    def test_new_day_posts_after_check(self, opener):
        request = opener.return_value.open
        request.side_effect = [
            self.response([{"body": "本文中の引用\n" + self.marker}]),
            BytesIO(),
        ]
        body = self.marker + "\n集計"
        self.assertTrue(digest.post_digest(body, self.issue_url))
        get, post = [call.args[0] for call in request.call_args_list]
        self.assertEqual([get.get_method(), post.get_method()], ["GET", "POST"])
        self.assertEqual(post.full_url, "https://gitlab.example/git/api/v4/projects/org%2Fteam%2Fhub/issues/42/notes")
        self.assertEqual(json.loads(post.data), {"body": body})

    @patch.object(digest, "build_opener")
    def test_invalid_list_fails_before_post(self, opener):
        request = opener.return_value.open
        request.return_value = self.response({"message": "error"})
        with self.assertRaises(ValueError):
            digest.post_digest(self.marker, self.issue_url)
        request.assert_called_once()
