from __future__ import annotations

import unittest

from _shared import AuditTestCase  # noqa: F401（環境隔離の副作用のため import）
from agent_audit import cleaning


class VersionTests(unittest.TestCase):
    def test_probe_finds_top_level_key(self):
        objs = [{"a": 1}, {"version": "2.1.3"}, {"version": "9.9.9"}]
        self.assertEqual(cleaning.probe_version(objs, "version"), "2.1.3")

    def test_probe_dotted_key(self):
        objs = [{"message": {"cli_version": "1.0"}}]
        self.assertEqual(cleaning.probe_version(objs, "message.cli_version"), "1.0")

    def test_probe_no_key_returns_empty(self):
        self.assertEqual(cleaning.probe_version([{"a": 1}], "version"), "")
        self.assertEqual(cleaning.probe_version([{"a": 1}], None), "")

    def test_probe_limit_ignores_later_entries(self):
        objs = [{"a": i} for i in range(60)] + [{"version": "3.0"}]
        self.assertEqual(cleaning.probe_version(objs, "version", limit=50), "")

    def test_version_matches_and_conditions(self):
        self.assertTrue(cleaning.version_matches("2.5.0", ">=2.0 <3.0"))
        self.assertFalse(cleaning.version_matches("3.0.0", ">=2.0 <3.0"))
        self.assertTrue(cleaning.version_matches("2.0.0", "=2.0"))
        self.assertFalse(cleaning.version_matches("", ">=2.0"))   # 未検出は常に不一致


class SelectRulesTests(unittest.TestCase):
    def test_no_spec_returns_no_rules(self):
        self.assertEqual(cleaning.select_rules(None, "1.0"), ([], ""))

    def test_falls_back_to_default_when_unmatched(self):
        spec = {"rules": [{"rule": "truncate-message", "max_chars": 10}],
                "versions": [{"when": ">=9.0", "rules": []}]}
        rules, when = cleaning.select_rules(spec, "1.0")
        self.assertEqual(rules, spec["rules"])
        self.assertEqual(when, "")

    def test_first_match_wins_and_replaces_wholesale(self):
        spec = {"rules": [{"rule": "truncate-message", "max_chars": 10}],
                "versions": [{"when": ">=2.0", "rules": [{"rule": "drop-message",
                                                          "prefixes": ["x"]}]},
                             {"when": ">=1.0", "rules": [{"rule": "drop-message",
                                                          "prefixes": ["y"]}]}]}
        rules, when = cleaning.select_rules(spec, "2.5")
        self.assertEqual(when, ">=2.0")
        self.assertEqual(rules, [{"rule": "drop-message", "prefixes": ["x"]}])   # 既定と混ざらない


class DropLineTests(unittest.TestCase):
    def test_equals_match(self):
        rules = [{"rule": "drop-line", "field": "isSidechain", "equals": True}]
        self.assertTrue(cleaning.should_drop_line({"isSidechain": True}, rules))
        self.assertFalse(cleaning.should_drop_line({"isSidechain": False}, rules))

    def test_in_match(self):
        rules = [{"rule": "drop-line", "field": "type", "in": ["progress", "snapshot"]}]
        self.assertTrue(cleaning.should_drop_line({"type": "progress"}, rules))
        self.assertFalse(cleaning.should_drop_line({"type": "message"}, rules))

    def test_missing_field_does_not_crash(self):
        warnings = []
        rules = [{"rule": "drop-line"}]
        self.assertFalse(cleaning.should_drop_line({"a": 1}, rules, warn=warnings.append))
        self.assertEqual(len(warnings), 1)


class CleanMessageTextTests(unittest.TestCase):
    def test_no_rules_passes_through_unchanged_unless_blank(self):
        self.assertEqual(cleaning.clean_message_text("  hi  ", []), "  hi  ")
        self.assertIsNone(cleaning.clean_message_text("   ", []))

    def test_drop_message_by_prefix(self):
        rules = [{"rule": "drop-message", "prefixes": ["Caveat: "]}]
        self.assertIsNone(cleaning.clean_message_text("Caveat: something", rules))
        self.assertEqual(cleaning.clean_message_text("keep me", rules), "keep me")

    def test_drop_message_by_contains(self):
        rules = [{"rule": "drop-message", "contains": ["noise-marker"]}]
        self.assertIsNone(cleaning.clean_message_text("has noise-marker inside", rules))

    def test_strip_tag_removes_block(self):
        rules = [{"rule": "strip-tag", "tags": ["system-reminder"]}]
        text = "before<system-reminder>hidden\nmore</system-reminder>after"
        self.assertEqual(cleaning.clean_message_text(text, rules), "beforeafter")

    def test_strip_tag_without_closing_tag_leaves_text(self):
        rules = [{"rule": "strip-tag", "tags": ["system-reminder"]}]
        text = "before<system-reminder>never closed"
        self.assertEqual(cleaning.clean_message_text(text, rules), text)

    def test_strip_regex_removes_match(self):
        rules = [{"rule": "strip-regex", "pattern": r"\d+"}]
        self.assertEqual(cleaning.clean_message_text("id=123 done", rules), "id= done")

    def test_strip_regex_invalid_pattern_skips_and_warns(self):
        warnings = []
        rules = [{"rule": "strip-regex", "pattern": "(unclosed"}]
        out = cleaning.clean_message_text("keep me", rules, warn=warnings.append)
        self.assertEqual(out, "keep me")
        self.assertEqual(len(warnings), 1)

    def test_truncate_message(self):
        rules = [{"rule": "truncate-message", "max_chars": 5}]
        out = cleaning.clean_message_text("abcdefghij", rules)
        self.assertTrue(out.startswith("abcde"))
        self.assertIn("truncated 5 chars", out)

    def test_unknown_rule_kind_warns_and_is_skipped(self):
        warnings = []
        rules = [{"rule": "no-such-rule"}]
        out = cleaning.clean_message_text("keep me", rules, warn=warnings.append)
        self.assertEqual(out, "keep me")
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
