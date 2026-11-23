"""gitlab_mail_teams のテスト。

    cd tools/gitlab-mail-teams
    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import EmailMessage
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPT = TOOL_DIR / "gitlab_mail_teams.py"

sys.path.insert(0, str(TOOL_DIR))

import gitlab_mail_teams as gmt  # noqa: E402

HOST = "https://gitlab.example.co.jp"


def load(name: str) -> dict:
    return gmt.parse_eml((FIXTURES / name).read_bytes())


def make_eml(subject: str, body: str, sender: str = "GitLab <gitlab@gitlab.example.co.jp>",
             headers: "dict[str, str] | None" = None, date: "str | None" = "Thu, 19 Nov 2026 10:00:00 +0900",
             ) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "hanako.sato@example.co.jp"
    msg["Subject"] = subject
    if date:
        msg["Date"] = date
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(body)
    return msg.as_bytes()


def run_cli(*args: str, stdin: "bytes | None" = None) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )


class EventShapeTest(unittest.TestCase):
    def test_every_fixture_has_all_fields_in_order(self):
        for name in ("issue_created.eml", "mr_review_requested.eml", "pipeline_failed.eml"):
            with self.subTest(name=name):
                event = load(name)
                self.assertEqual(tuple(event.keys()), gmt.EVENT_FIELDS)
                self.assertIn(event["kind"], gmt.KINDS)
                self.assertTrue(event["subject"])
                self.assertTrue(event["sender"])


class IssueCreatedTest(unittest.TestCase):
    def setUp(self):
        self.event = load("issue_created.eml")

    def test_fields(self):
        e = self.event
        self.assertEqual(e["kind"], "issue_created")
        self.assertEqual(e["project"], "platform/order-service")
        self.assertEqual(e["title"], "Stock reservation times out on order confirmation")
        self.assertEqual(e["number"], 128)
        self.assertEqual(e["actor"], "Taro Yamada")
        self.assertEqual(e["url"], f"{HOST}/platform/order-service/-/issues/128")
        self.assertEqual(e["status"], "opened")
        self.assertIsNone(e["branch"])
        self.assertEqual(e["received_at"], "2026-11-18T09:12:45+09:00")
        self.assertEqual(
            e["subject"],
            "order-service | Stock reservation times out on order confirmation (#128)")
        self.assertIn("gitlab@gitlab.example.co.jp", e["sender"])
        self.assertIn("Taro Yamada", e["sender"])

    def test_summary_is_description_only(self):
        summary = self.event["summary"]
        self.assertTrue(summary.startswith("夕方のピーク時間帯に"), summary)
        self.assertNotIn("created an issue", summary)
        self.assertNotIn("Assignee", summary)
        self.assertNotIn("View it on GitLab", summary)
        self.assertNotIn("<p", summary)
        self.assertLessEqual(len(summary), gmt.SUMMARY_MAX)


class MergeRequestReviewTest(unittest.TestCase):
    def setUp(self):
        self.event = load("mr_review_requested.eml")

    def test_fields(self):
        e = self.event
        self.assertEqual(e["kind"], "mr_review_requested")
        self.assertEqual(e["project"], "platform/order-service")
        self.assertEqual(e["title"], "Add timeout and retry to stock reservation")
        self.assertEqual(e["number"], 57)
        self.assertEqual(e["actor"], "Ichiro Suzuki")
        self.assertEqual(e["url"], f"{HOST}/platform/order-service/-/merge_requests/57")
        self.assertEqual(e["status"], "review_requested")
        self.assertEqual(e["branch"], "feature/stock-reserve-timeout")
        self.assertEqual(e["received_at"], "2026-11-18T14:03:27+09:00")

    def test_summary_skips_lead_and_field_lines(self):
        summary = self.event["summary"]
        self.assertTrue(summary.startswith("在庫引当の呼び出しに"), summary)
        self.assertNotIn("Branches", summary)
        self.assertNotIn("Reviewer", summary)
        self.assertNotIn("Unsubscribe", summary)

    def test_mention_on_merge_request_is_review_requested(self):
        event = gmt.parse_eml(make_eml(
            "order-service | Add timeout and retry to stock reservation (!57)",
            "@h-sato この再試行の間隔で問題ないか見てもらえますか。\n\n-- \n"
            f"View it on GitLab: {HOST}/platform/order-service/-/merge_requests/57#note_901\n",
            sender='"Ichiro Suzuki (@i-suzuki)" <gitlab@gitlab.example.co.jp>',
            headers={
                "X-GitLab-Project-Path": "platform/order-service",
                "X-GitLab-MergeRequest-IID": "57",
                "X-GitLab-NotificationReason": "mentioned",
                "In-Reply-To": "<merge_request_9034@gitlab.example.co.jp>",
            }))
        self.assertEqual(event["kind"], "mr_review_requested")
        self.assertEqual(event["number"], 57)
        self.assertEqual(event["actor"], "Ichiro Suzuki")


class PipelineFailedTest(unittest.TestCase):
    def setUp(self):
        self.event = load("pipeline_failed.eml")

    def test_fields(self):
        e = self.event
        self.assertEqual(e["kind"], "pipeline_failed")
        self.assertEqual(e["project"], "platform/order-service")
        self.assertEqual(e["title"], "Failed pipeline for main")
        self.assertEqual(e["number"], 30417)
        self.assertEqual(e["actor"], "Ichiro Suzuki")
        self.assertEqual(e["url"], f"{HOST}/platform/order-service/-/pipelines/30417")
        self.assertEqual(e["status"], "failed")
        self.assertEqual(e["branch"], "main")
        self.assertEqual(e["received_at"], "2026-11-19T02:41:09+00:00")
        self.assertIn("gitlab@gitlab.example.co.jp", e["sender"])

    def test_summary_has_commit_message_and_failed_jobs(self):
        summary = self.event["summary"]
        self.assertIn("在庫引当のタイムアウトを 3 秒にする", summary)
        self.assertIn("test/unit-test", summary)
        self.assertIn("test/integration-test", summary)


class UnknownFallbackTest(unittest.TestCase):
    def test_successful_pipeline_is_unknown_but_filled(self):
        event = gmt.parse_eml(make_eml(
            "order-service | Successful pipeline for main | 9f2c41d7",
            "Pipeline #30418 has passed!\n\n"
            f"Project: order-service ( {HOST}/platform/order-service )\n"
            f"Branch: main ( {HOST}/platform/order-service/-/commits/main )\n\n"
            "Commit Message: retry the flaky test\n\n"
            f"Pipeline #30418 ( {HOST}/platform/order-service/-/pipelines/30418 ) "
            f"triggered by Ichiro Suzuki ( {HOST}/i-suzuki )\n"
            "successfully completed 6 jobs in 3 stages.\n",
            headers={
                "X-GitLab-Project-Path": "platform/order-service",
                "X-GitLab-Pipeline-Id": "30418",
                "X-GitLab-Pipeline-Ref": "main",
                "X-GitLab-Pipeline-Status": "success",
            }))
        self.assertEqual(event["kind"], "unknown")
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["number"], 30418)
        self.assertEqual(event["branch"], "main")
        self.assertEqual(event["actor"], "Ichiro Suzuki")
        self.assertEqual(event["url"], f"{HOST}/platform/order-service/-/pipelines/30418")
        self.assertEqual(event["project"], "platform/order-service")

    def test_issue_comment_is_unknown_but_filled(self):
        event = gmt.parse_eml(make_eml(
            "Re: order-service | Stock reservation times out on order confirmation (#128)",
            "Hanako Sato commented:\n\n"
            "在庫サービス側のログも確認します。\n\n-- \n"
            f"View it on GitLab: {HOST}/platform/order-service/-/issues/128#note_877\n",
            sender='"Hanako Sato (@h-sato)" <gitlab@gitlab.example.co.jp>',
            headers={
                "X-GitLab-Project-Path": "platform/order-service",
                "X-GitLab-Issue-IID": "128",
                "X-GitLab-NotificationReason": "subscribed",
                "In-Reply-To": "<issue_4521@gitlab.example.co.jp>",
            }))
        self.assertEqual(event["kind"], "unknown")
        self.assertEqual(event["number"], 128)
        self.assertEqual(event["title"], "Stock reservation times out on order confirmation")
        self.assertEqual(event["actor"], "Hanako Sato")
        self.assertEqual(event["url"], f"{HOST}/platform/order-service/-/issues/128")
        self.assertEqual(event["summary"], "在庫サービス側のログも確認します。")
        self.assertIsNone(event["status"])

    def test_issue_assignment_only_is_unknown_but_filled(self):
        # 返信でもコメントでもないが、作成の証拠も無い（担当者が変わっただけ）。
        event = gmt.parse_eml(make_eml(
            "order-service | Stock reservation times out on order confirmation (#128)",
            "Assignee changed to Hanako Sato\n\n-- \n"
            f"View it on GitLab: {HOST}/platform/order-service/-/issues/128\n"
            "You're receiving this email because you have been assigned an item.\n",
            sender='"Taro Yamada (@t-yamada)" <gitlab@gitlab.example.co.jp>',
            headers={
                "X-GitLab-Project-Path": "platform/order-service",
                "X-GitLab-Issue-IID": "128",
                "X-GitLab-NotificationReason": "assigned",
            }))
        self.assertEqual(event["kind"], "unknown")
        self.assertIsNone(event["status"])
        self.assertEqual(event["number"], 128)
        self.assertEqual(event["title"], "Stock reservation times out on order confirmation")
        self.assertEqual(event["project"], "platform/order-service")
        self.assertEqual(event["actor"], "Taro Yamada")
        self.assertEqual(event["url"], f"{HOST}/platform/order-service/-/issues/128")

    def test_issue_label_and_close_notifications_are_unknown(self):
        for body in ("Added ~bug label\n", "Issue was closed by Taro Yamada\n"):
            with self.subTest(body=body):
                event = gmt.parse_eml(make_eml(
                    "order-service | Stock reservation times out on order confirmation (#128)",
                    body, headers={"X-GitLab-Issue-IID": "128",
                                   "X-GitLab-NotificationReason": "subscribed"}))
                self.assertEqual(event["kind"], "unknown")
                self.assertEqual(event["number"], 128)

    def test_lead_line_with_long_url_stays_out_of_summary(self):
        long_url = f"{HOST}/groups/platform/" + "a" * 150
        event = gmt.parse_eml(make_eml(
            "order-service | Stock reservation times out on order confirmation (#128)",
            f"Taro Yamada ( {long_url} ) created an issue: "
            f"{HOST}/platform/order-service/-/issues/128\n\n再現手順を書きます。\n",
            headers={"X-GitLab-Issue-IID": "128"}))
        self.assertEqual(event["kind"], "issue_created")
        self.assertEqual(event["summary"], "再現手順を書きます。")

    def test_html_links_with_any_quoting_keep_their_url(self):
        text = gmt._html_to_text(
            f"<p><a href='{HOST}/a/-/issues/1'>one</a> "
            f"<a class=x href={HOST}/a/-/issues/2>two</a> "
            f'<a href="{HOST}/a/-/issues/3" target="_blank">three</a></p>')
        self.assertIn(f"one ( {HOST}/a/-/issues/1 )", text)
        self.assertIn(f"two ( {HOST}/a/-/issues/2 )", text)
        self.assertIn(f"three ( {HOST}/a/-/issues/3 )", text)

    def test_body_falls_back_to_declared_charset(self):
        self.assertEqual(gmt._decode_bytes("在庫".encode("shift_jis"), "shift_jis"), "在庫")
        self.assertEqual(gmt._decode_bytes("在庫".encode("utf-8"), "x-no-such-charset"), "在庫")
        self.assertEqual(gmt._decode_bytes("在庫".encode("utf-8"), None), "在庫")

    def test_non_gitlab_mail_keeps_subject_and_sender(self):
        event = gmt.parse_eml(make_eml(
            "来週の定例について", "来週の定例は 15 時からに変更します。\n",
            sender="Jiro Tanaka <jiro.tanaka@example.co.jp>"))
        self.assertEqual(event["kind"], "unknown")
        self.assertEqual(event["subject"], "来週の定例について")
        self.assertEqual(event["title"], "来週の定例について")
        self.assertIn("jiro.tanaka@example.co.jp", event["sender"])
        self.assertIsNone(event["project"])
        self.assertIsNone(event["number"])
        self.assertIsNone(event["url"])
        self.assertEqual(event["summary"], "来週の定例は 15 時からに変更します。")
        self.assertEqual(event["received_at"], "2026-11-19T10:00:00+09:00")

    def test_attached_email_does_not_override_html_body(self):
        msg = EmailMessage()
        msg["Subject"] = "GitLab notification"
        msg.set_content("<p>Notification body</p>", subtype="html")
        attached = EmailMessage()
        attached.set_content("Attached email")
        msg.add_attachment(attached)

        event = gmt.parse_eml(msg.as_bytes())
        self.assertEqual(event["summary"], "Notification body")

    def test_missing_date_gives_null_received_at(self):
        event = gmt.parse_eml(make_eml("hello", "body\n", date=None))
        self.assertIsNone(event["received_at"])

    def test_encoded_japanese_subject_is_decoded(self):
        # 件名と差出人は RFC 2047（B 符号化）、本文は base64 で届く形を手で組む。
        def b64(text: str) -> str:
            return base64.b64encode(text.encode("utf-8")).decode("ascii")

        def word(text: str) -> str:
            return f"=?UTF-8?B?{b64(text)}?="

        body = (f"山田 太郎 created an issue: {HOST}/platform/order-service/-/issues/129\n\n"
                "再現手順を書きます。\n")
        raw = "\r\n".join([
            f"From: {word('山田 太郎 (@t-yamada)')} <gitlab@gitlab.example.co.jp>",
            "To: hanako.sato@example.co.jp",
            f"Subject: {word('order-service | 注文確定時に在庫引当がタイムアウトする (#129)')}",
            "Date: Thu, 19 Nov 2026 10:00:00 +0900",
            "X-GitLab-Project-Path: platform/order-service",
            "X-GitLab-Issue-IID: 129",
            "MIME-Version: 1.0",
            "Content-Type: text/plain; charset=UTF-8",
            "Content-Transfer-Encoding: base64",
            "",
            b64(body),
            "",
        ]).encode("ascii")
        event = gmt.parse_eml(raw)
        self.assertEqual(event["kind"], "issue_created")
        self.assertEqual(event["title"], "注文確定時に在庫引当がタイムアウトする")
        self.assertEqual(event["actor"], "山田 太郎")
        self.assertEqual(event["summary"], "再現手順を書きます。")

    def test_unparsable_input_raises(self):
        for data in (b"", b"   \n\n", b"this is not an email at all\n", b"\x00\xff\xfe\x01"):
            with self.subTest(data=data):
                with self.assertRaises(gmt.ParseError):
                    gmt.parse_eml(data)


class CardTest(unittest.TestCase):
    def assert_card_shape(self, card: dict):
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["version"], "1.4")
        self.assertEqual(card["$schema"], "http://adaptivecards.io/schemas/adaptive-card.json")
        self.assertIsInstance(card["body"], list)
        self.assertGreaterEqual(len(card["body"]), 2)
        for block in card["body"]:
            self.assertIn(block["type"], ("TextBlock", "FactSet"))
            if block["type"] == "TextBlock":
                self.assertTrue(block["text"])
                self.assertTrue(block["wrap"])
            else:
                for fact in block["facts"]:
                    self.assertTrue(fact["title"])
                    self.assertIsInstance(fact["value"], str)
                    self.assertTrue(fact["value"])
        json.dumps(card)  # JSON にできること

    def facts(self, card: dict) -> dict:
        for block in card["body"]:
            if block["type"] == "FactSet":
                return {f["title"]: f["value"] for f in block["facts"]}
        return {}

    def test_cards_for_fixtures(self):
        expected_urls = {
            "issue_created.eml": f"{HOST}/platform/order-service/-/issues/128",
            "mr_review_requested.eml": f"{HOST}/platform/order-service/-/merge_requests/57",
            "pipeline_failed.eml": f"{HOST}/platform/order-service/-/pipelines/30417",
        }
        for name, url in expected_urls.items():
            with self.subTest(name=name):
                card = gmt.build_card(load(name))
                self.assert_card_shape(card)
                self.assertEqual(len(card["actions"]), 1)
                action = card["actions"][0]
                self.assertEqual(action["type"], "Action.OpenUrl")
                self.assertEqual(action["url"], url)
                self.assertTrue(action["title"])
                self.assertEqual(self.facts(card)["プロジェクト"], "platform/order-service")

    def test_issue_card_title_has_number(self):
        card = gmt.build_card(load("issue_created.eml"))
        self.assertEqual(
            card["body"][1]["text"], "Stock reservation times out on order confirmation (#128)")

    def test_mr_card_title_and_branch(self):
        card = gmt.build_card(load("mr_review_requested.eml"))
        self.assertEqual(card["body"][1]["text"], "Add timeout and retry to stock reservation (!57)")
        self.assertEqual(self.facts(card)["ブランチ"], "feature/stock-reserve-timeout")

    def test_pipeline_card_is_marked_as_failure(self):
        card = gmt.build_card(load("pipeline_failed.eml"))
        self.assertEqual(card["body"][0]["color"], "Attention")
        self.assertEqual(card["body"][1]["text"], "Failed pipeline for main")
        facts = self.facts(card)
        self.assertEqual(facts["パイプライン"], "#30417")
        self.assertEqual(facts["ブランチ"], "main")
        self.assertEqual(facts["状態"], "failed")

    def test_mail_derived_text_is_not_rendered_as_markdown(self):
        event = gmt.parse_eml(make_eml(
            "order-service | **bold** [link](https://evil.example/) (#128)",
            f"Taro Yamada created an issue: {HOST}/platform/order-service/-/issues/128\n\n"
            "# 見出し *強調* [ここ](https://evil.example/)\n",
            headers={"X-GitLab-Project-Path": "platform/order-service",
                     "X-GitLab-Issue-IID": "128"}))
        card = gmt.build_card(event)
        self.assert_card_shape(card)
        blocks = card["body"]
        self.assertNotIn("markdown", blocks[0])       # 見出しはこちらで決めた固定の文言
        self.assertIs(blocks[1]["markdown"], False)   # 題名
        self.assertIn("**bold**", blocks[1]["text"])
        self.assertEqual(blocks[-1]["text"], event["summary"])
        self.assertIs(blocks[-1]["markdown"], False)  # 要約
        fact_sets = [b for b in blocks if b["type"] == "FactSet"]
        self.assertEqual(len(fact_sets), 1)
        for fact in fact_sets[0]["facts"]:
            self.assertIs(fact["markdown"], False, fact)

    def test_unknown_card_without_url_has_no_actions(self):
        event = gmt.parse_eml(make_eml("hello", "no links here\n"))
        card = gmt.build_card(event)
        self.assert_card_shape(card)
        self.assertNotIn("actions", card)
        self.assertNotIn("color", card["body"][0])
        self.assertEqual(card["body"][1]["text"], "hello")


class CliTest(unittest.TestCase):
    def test_json_is_default_format(self):
        proc = run_cli(str(FIXTURES / "issue_created.eml"))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        event = json.loads(out)
        self.assertEqual(event, load("issue_created.eml"))
        self.assertIn("夕方のピーク時間帯", out)  # ensure_ascii=False
        self.assertNotIn("\\u", out)
        self.assertIn('\n  "kind": "issue_created"', out)  # indent=2

    def test_format_json_explicit(self):
        proc = run_cli(str(FIXTURES / "pipeline_failed.eml"), "--format", "json")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(proc.stdout.decode("utf-8"))["kind"], "pipeline_failed")

    def test_format_card(self):
        proc = run_cli(str(FIXTURES / "mr_review_requested.eml"), "--format", "card")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        card = json.loads(out)
        self.assertEqual(card, gmt.build_card(load("mr_review_requested.eml")))
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["version"], "1.4")
        self.assertEqual(card["actions"][0]["type"], "Action.OpenUrl")
        self.assertIn("レビュー", out)
        self.assertIn('\n  "type": "AdaptiveCard"', out)

    def test_reads_stdin_when_path_is_omitted_or_dash(self):
        data = (FIXTURES / "pipeline_failed.eml").read_bytes()
        for args in ((), ("-",), ("-", "--format", "card")):
            with self.subTest(args=args):
                proc = run_cli(*args, stdin=data)
                self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
                self.assertTrue(json.loads(proc.stdout.decode("utf-8")))

    def test_unparsable_input_exits_2_with_error_on_stderr(self):
        proc = run_cli("-", stdin=b"this is not an email at all\n")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("error", proc.stderr.decode("utf-8", "replace"))

    def test_empty_input_exits_2(self):
        proc = run_cli(stdin=b"")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertTrue(proc.stderr.strip())

    def test_missing_file_exits_2(self):
        proc = run_cli(str(FIXTURES / "no-such-file.eml"))
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.strip())

    def test_main_in_process(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gmt.main([str(FIXTURES / "issue_created.eml"), "--format", "card"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["type"], "AdaptiveCard")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
