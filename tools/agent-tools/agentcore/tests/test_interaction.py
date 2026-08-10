import json
import pathlib
import unittest
from datetime import datetime, timezone

from agentcore import interaction


class InteractionTests(unittest.TestCase):
    def test_modes_match_public_schema(self):
        schema = pathlib.Path(__file__).parents[4] / "schemas" / "agent-interaction.schema.json"
        raw = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(set(raw["$defs"]["request"]["properties"]["mode"]["enum"]), interaction.MODES)

    def test_builds_finite_path_safe_approval_request(self):
        request = interaction.build_request(
            "run" + "/" + "unsafe", "node unsafe",
            {"mode": "approval", "prompt": "進めますか", "audience": ["reviewer"]},
            now=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.assertRegex(request["interaction_id"], r"^ix-[0-9a-f]{16}$")
        self.assertEqual(request["mode"], "approval")
        self.assertEqual(request["audience"], ["reviewer"])
        self.assertEqual(request["created_at"], "2026-08-10T00:00:00Z")
        self.assertEqual(request["expires_at"], "2026-08-17T00:00:00Z")

    def test_validates_approval_response(self):
        request = interaction.build_request(
            "run", "node", {"mode": "approval", "prompt": "進めますか"},
            now=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        response = interaction.validate_response(request, {
            "version": 1,
            "interaction_id": request["interaction_id"],
            "response_id": "01JABC",
            "actor": "dashboard-user",
            "answer": {"decision": "approved", "comment": "確認済み"},
            "submitted_at": "2026-08-10T01:00:00Z",
        })
        self.assertEqual(response["answer"], {"decision": "approved", "comment": "確認済み"})

    def test_resolves_concurrent_responses_by_response_id(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        request = interaction.build_request("run", "node", {
            "mode": "approval", "prompt": "進めますか",
        }, now=now)
        responses = [{
            "version": 1, "interaction_id": request["interaction_id"],
            "response_id": response_id, "actor": actor,
            "answer": {"decision": decision}, "submitted_at": "2026-08-10T01:00:00Z",
        } for response_id, actor, decision in [
            ("02-later", "second", "rejected"),
            ("01-first", "first", "approved"),
        ]]
        resolution = interaction.resolve(request, responses, now=now)
        self.assertEqual(resolution["response_id"], "01-first")
        self.assertEqual(resolution["outcome"], "approved")
        self.assertEqual(resolution["actor"], "first")

    def test_expired_approval_fails_closed(self):
        request = interaction.build_request(
            "run", "node", {"mode": "approval", "prompt": "進めますか", "timeout_seconds": 60},
            now=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        resolution = interaction.resolve(
            request, [], now=datetime(2026, 8, 10, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(resolution["outcome"], "expired")
        self.assertEqual(resolution["actor"], "system")

    def test_expired_choice_uses_only_explicit_default(self):
        request = interaction.build_request("run", "node", {
            "mode": "choice", "prompt": "どちらにしますか", "timeout_seconds": 60,
            "options": ["safe", "fast"], "default_option": "safe",
        }, now=datetime(2026, 8, 10, tzinfo=timezone.utc))
        resolution = interaction.resolve(
            request, [], now=datetime(2026, 8, 10, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(resolution["outcome"], "defaulted")
        self.assertEqual(resolution["answer"], {"option": "safe"})

    def test_late_response_does_not_override_expiry(self):
        request = interaction.build_request(
            "run", "node", {"mode": "approval", "prompt": "進めますか", "timeout_seconds": 60},
            now=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        response = {
            "version": 1, "interaction_id": request["interaction_id"],
            "response_id": "01-late", "actor": "user",
            "answer": {"decision": "approved"}, "submitted_at": "2026-08-10T00:02:00Z",
        }
        resolution = interaction.resolve(
            request, [response], now=datetime(2026, 8, 10, 0, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(resolution["outcome"], "expired")


if __name__ == "__main__":
    unittest.main()
