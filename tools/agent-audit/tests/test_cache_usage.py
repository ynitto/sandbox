"""Offline native-format fixtures and ledger compatibility regressions."""
import json
from pathlib import Path
from unittest.mock import patch

from _shared import AuditTestCase, collect, readers, stats, usage


class CacheUsageTests(AuditTestCase):
    def session(self, rows):
        path = Path(self.tmp) / "native" / "s.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(json.dumps({"timestamp": "2026-09-22T10:00:00Z", **r})
                                  for r in rows))
        return readers.read_sessions({"format": "jsonl-dir", "paths": [str(path.parent)]})[0]

    def test_claude_separate_components_and_last_message_revision(self):
        def message(output):
            return {"message": {"id": "m1", "usage": {
                "input_tokens": 2, "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 10, "output_tokens": output}}}
        s = self.session([message(1), message(5)])
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (42, 5))
        self.assertEqual(s["usage_breakdown"], {
            "input_total": 42, "input_uncached": 2, "cache_read": 10,
            "cache_write": 30, "output": 5,
            "semantics": "anthropic-separate-input-components", "completeness": "complete"})

    def test_codex_last_cumulative_wins_without_double_counting(self):
        def total(i, c):
            return {"payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": i, "cached_input_tokens": c, "output_tokens": 20}}}}
        s = self.session([{"usage": {"input_tokens": 999, "output_tokens": 999}},
                          total(50, 40), total(100, 80), total(100, 80)])
        self.assertEqual(s["tokens_in"], 180)  # Deliberately preserved legacy contract.
        self.assertEqual(s["tokens_out"], 20)
        b = s["usage_breakdown"]
        self.assertEqual((b["input_total"], b["input_uncached"], b["cache_read"]), (100, 20, 80))
        self.assertIsNone(b["cache_write"])
        self.assertEqual(b["completeness"], "partial")

    def test_openai_nested_details(self):
        b = readers._usage_components({"input_tokens": 100, "output_tokens": 8,
                                       "input_tokens_details": {"cached_tokens": 60}})
        self.assertEqual((b["input_total"], b["input_uncached"], b["cache_read"]), (100, 40, 60))

    def test_flat_legacy_and_missing_output(self):
        s = self.session([{"kind": "llm_progress", "tokens_out": 999},
                          {"kind": "llm_end", "tokens_in": 100, "tokens_out": 5},
                          {"kind": "llm_end", "tokens_in": 200}])
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (300, 5))
        b = s["usage_breakdown"]
        self.assertEqual(b["input_total"], 300)
        for k in ("cache_read", "cache_write", "input_uncached", "output"):
            self.assertIsNone(b[k])

    def test_missing_llm_end_invalidates_session_totals(self):
        s = self.session([{"kind": "llm_end", "tokens_in": 100, "tokens_out": 5},
                          {"kind": "llm_end"}])
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (100, 5))
        self.assertIsNone(s["usage_breakdown"]["input_total"])
        self.assertIsNone(s["usage_breakdown"]["output"])

    def test_partial_unknown_invalid_and_explicit_zero(self):
        for u in ({"input_tokens": 100},
                  {"input_tokens": 100, "cache_read_input_tokens": 20},
                  {"input_tokens": 100, "cache_read_input_tokens": 20, "cached_input_tokens": 30}):
            self.assertIsNone(readers._usage_components(u)["input_total"])
        for bad in (None, -1, True, "10", 1.5):
            b = readers._usage_components({"input_tokens": 100, "cached_input_tokens": bad})
            self.assertIsNone(b["cache_read"])
            self.assertIsNone(b["input_uncached"])
        b = readers._usage_components({"input_tokens": 100, "cached_input_tokens": 101})
        self.assertIsNone(b["input_uncached"])
        b = readers._usage_components({"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0})
        self.assertEqual(b["input_uncached"], 0)
        self.assertEqual(b["cache_read"], 0)

    def test_partial_call_does_not_become_full_session_total(self):
        s = self.session([{"usage": {"input_tokens": 2, "cache_read_input_tokens": 10,
                                      "cache_creation_input_tokens": 30, "output_tokens": 5}},
                          {"usage": {"input_tokens": 4, "cache_read_input_tokens": 20,
                                      "output_tokens": 7}}])
        b = s["usage_breakdown"]
        self.assertIsNone(b["input_total"])
        self.assertIsNone(b["cache_write"])
        self.assertEqual(b["cache_read"], 30)
        self.assertEqual(b["output"], 12)

    def test_collect_replays_old_record_preserves_breakdown_and_is_idempotent(self):
        sess = self.session([{"usage": {"input_tokens": 2, "cache_read_input_tokens": 10,
                                         "cache_creation_input_tokens": 30, "output_tokens": 5}}])
        st = self.make_store()
        spec = {"session_log": {"format": "jsonl-dir", "paths": [str(Path(sess["store"]).parent)],
                                "usage": True}}
        with patch.object(collect, "agent_defs_with_session_log", return_value=[("claude", spec)]):
            with patch.object(collect, "SESSION_PARSER_REVISION", 3), patch.object(
                    readers, "read_sessions", return_value=[{k: v for k, v in sess.items()
                                                           if k != "usage_breakdown"}]):
                self.assertEqual(collect.collect_cli_native(self.make_args(), st, with_transcripts=False), 1)
            self.assertEqual(collect.collect_cli_native(self.make_args(), st, with_transcripts=False), 1)
            self.assertEqual(collect.collect_cli_native(self.make_args(), st, with_transcripts=False), 0)
        records = list(st.iter_records())
        self.assertNotIn("usage_breakdown", records[0])
        self.assertEqual(records[1]["usage_breakdown"], sess["usage_breakdown"])
        sessions = usage.load_period_records(st, "total")[1]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["usage_breakdown"], sess["usage_breakdown"])

    def test_ratings_mixed_old_new_samples_and_weighted_ratio(self):
        st = self.make_store()
        components = [None,
                      readers._usage_components({"input_tokens": 20, "cache_read_input_tokens": 60,
                                                  "cache_creation_input_tokens": 20, "output_tokens": 5}),
                      readers._usage_components({"input_tokens": 300, "cached_input_tokens": 30,
                                                  "output_tokens": 15})]
        for i, b in enumerate(components):
            ts = f"2026-09-22T1{i}:00:00Z"
            st.append_record({"id": f"l{i}", "ts": ts, "kind": "ledger", "purpose": "work",
                              "agent_cli": "claude", "model": "m", "seconds": 1})
            st.append_record({"id": f"s{i}", "session_id": f"s{i}", "ts": ts, "started_at": ts,
                              "kind": "session", "agent_cli": "claude", "model": "m",
                              "measured": True, "tokens_in": 100, "tokens_out": 10,
                              **({"usage_breakdown": b} if b else {})})
        row = stats.aggregate_ratings(self.make_args(), st, "total")[0]
        self.assertEqual(row["usage_runs"], 3)
        self.assertEqual(row["average_tokens"], 110)
        self.assertEqual(row["average_input_total"], 200)
        self.assertEqual(row["average_input_total_samples"], 2)
        self.assertEqual(row["average_input_uncached_samples"], 2)
        self.assertEqual(row["average_cache_read_samples"], 2)
        self.assertEqual(row["average_output_samples"], 2)
        self.assertEqual(row["average_cache_write_samples"], 1)
        self.assertEqual(row["average_cache_read"], 45)
        self.assertEqual(row["cache_read_ratio"], .225)
        self.assertEqual(row["cache_read_ratio_samples"], 2)
        self.assertEqual(row["cache_read_ratio_input_total"], 400)

    def test_ratio_excludes_unpaired_and_zero_denominator(self):
        self.assertNotIn("cache_read_ratio", stats._cache_metrics([
            {"cache_read": 20}, {"input_total": 0, "cache_read": 0}]))
