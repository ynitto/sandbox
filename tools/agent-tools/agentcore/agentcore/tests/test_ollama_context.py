from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from agentcore import ollama_context


def _json_response(payload: dict):
    body = io.BytesIO(json.dumps(payload).encode())
    body.__enter__ = lambda self: self
    body.__exit__ = lambda *_a: None
    return body


class TestResolveLimit(unittest.TestCase):
    def setUp(self):
        ollama_context._limit_cache.clear()

    def test_explicit_wins(self):
        with mock.patch.object(ollama_context, "_get_json") as get:
            limit, source = ollama_context.resolve_limit("m", explicit=4096)
        self.assertEqual((limit, source), (4096, "explicit"))
        self.assertEqual(get.call_count, 0, "宣言があるなら問い合わせない")

    def test_num_ctx_option_wins_over_the_server(self):
        """こちらが num_ctx を送るなら、サーバはその値で確保する（宣言が効く）。"""
        with mock.patch.object(ollama_context, "_get_json") as get:
            limit, source = ollama_context.resolve_limit("m", options={"num_ctx": 8192})
        self.assertEqual((limit, source), (8192, "options"))
        self.assertEqual(get.call_count, 0)

    def test_falls_back_to_ps_then_show(self):
        calls = []

        def fake(_host, path, body=None):
            calls.append(path)
            if path == "/api/ps":
                return {"models": [{"name": "qwen3:9b", "context_length": 16384}]}
            return None

        with mock.patch.object(ollama_context, "_get_json", fake):
            self.assertEqual(ollama_context.resolve_limit("qwen3:9b"), (16384, "server"))
        self.assertEqual(calls, ["/api/ps"])

        ollama_context._limit_cache.clear()

        def only_show(_host, path, body=None):
            if path == "/api/show":
                return {"model_info": {"general.architecture": "qwen3",
                                       "qwen3.context_length": 32768}}
            return {"models": []}

        with mock.patch.object(ollama_context, "_get_json", only_show):
            self.assertEqual(ollama_context.resolve_limit("qwen3:9b"), (32768, "model"))

    def test_unknown_when_nothing_answers(self):
        with mock.patch.object(ollama_context, "_get_json", lambda *_a, **_k: None):
            self.assertEqual(ollama_context.resolve_limit("m"), (0, "unknown"))

    def test_failures_are_not_cached(self):
        """一度取れなかっただけで、以後ずっと文脈表示が死ぬのは割に合わない。"""
        with mock.patch.object(ollama_context, "_get_json", lambda *_a, **_k: None):
            ollama_context.resolve_limit("m")
        with mock.patch.object(ollama_context, "_get_json",
                               lambda _h, path, body=None:
                               {"models": [{"name": "m", "context_length": 2048}]}
                               if path == "/api/ps" else None):
            self.assertEqual(ollama_context.resolve_limit("m"), (2048, "server"))

    def test_success_is_cached(self):
        calls = []

        def fake(_host, path, body=None):
            calls.append(path)
            return {"models": [{"name": "m", "context_length": 2048}]}

        with mock.patch.object(ollama_context, "_get_json", fake):
            ollama_context.resolve_limit("m")
            ollama_context.resolve_limit("m")
        self.assertEqual(len(calls), 1)

    def test_metadata_failures_never_raise(self):
        """上限が分からないだけで実行は続く（ここで落とすと本末転倒）。"""
        with mock.patch.object(ollama_context.urllib.request, "urlopen",
                               side_effect=OSError("boom")):
            self.assertEqual(ollama_context.resolve_limit("m"), (0, "unknown"))


class TestContextTracker(unittest.TestCase):
    def test_measured_from_the_server(self):
        tracker = ollama_context.ContextTracker(limit=1000)
        snap = tracker.observe(tokens_in=400, tokens_out=100)
        self.assertEqual(snap["context_used"], 500)
        self.assertEqual(snap["context_limit"], 1000)
        self.assertEqual(snap["context_pct"], 50.0)
        self.assertEqual(snap["context_source"], "measured")

    def test_cache_hit_underreport_is_corrected(self):
        """キャッシュ命中で「新規評価分だけ」返す版でも、使用量は減らない。

        会話は伸びる一方なので、減って見えたら差分報告とみなして積み上げる。
        """
        tracker = ollama_context.ContextTracker(limit=10_000)
        tracker.observe(tokens_in=3000, tokens_out=200)      # 1 ラウンド目: 3200
        tracker.add_text("あ" * 300)                          # 次の入力に足した分
        snap = tracker.observe(tokens_in=50, tokens_out=100)  # 差分だけ報告された
        self.assertGreater(snap["context_used"], 3200)
        self.assertEqual(snap["context_source"], "estimated")

    def test_no_limit_reports_usage_without_pct(self):
        tracker = ollama_context.ContextTracker(limit=0)
        snap = tracker.observe(tokens_in=10, tokens_out=5)
        self.assertEqual(snap["context_used"], 15)
        self.assertNotIn("context_pct", snap)
        self.assertIsNone(tracker.pct())

    def test_warns_once_after_crossing_the_threshold(self):
        tracker = ollama_context.ContextTracker(limit=1000, warn_pct=90)
        tracker.observe(tokens_in=800, tokens_out=0)
        self.assertFalse(tracker.should_warn())
        tracker.observe(tokens_in=950, tokens_out=0)
        self.assertTrue(tracker.should_warn())
        self.assertFalse(tracker.should_warn(), "同じ警告を毎ラウンド出さない")

    def test_unknown_limit_never_warns(self):
        tracker = ollama_context.ContextTracker(limit=0)
        tracker.observe(tokens_in=10_000_000, tokens_out=0)
        self.assertFalse(tracker.should_warn(), "知らない上限を根拠に警告しない")

    def test_remaining_accounts_for_reserve_and_pending(self):
        tracker = ollama_context.ContextTracker(limit=1000)
        tracker.observe(tokens_in=400, tokens_out=0)
        self.assertEqual(tracker.remaining_tokens(reserve=100), 500)
        tracker.add_text("x" * int(300 * ollama_context.CHARS_PER_TOKEN))
        self.assertEqual(tracker.remaining_tokens(reserve=100), 200)
        self.assertEqual(tracker.remaining_chars(reserve=100),
                         int(200 * ollama_context.CHARS_PER_TOKEN))

    def test_remaining_is_unlimited_when_the_limit_is_unknown(self):
        tracker = ollama_context.ContextTracker(limit=0)
        self.assertEqual(tracker.remaining_tokens(), -1)
        self.assertEqual(tracker.remaining_chars(), -1)

    def test_remaining_never_goes_negative(self):
        tracker = ollama_context.ContextTracker(limit=100)
        tracker.observe(tokens_in=500, tokens_out=0)
        self.assertEqual(tracker.remaining_tokens(), 0)


if __name__ == "__main__":
    unittest.main()
