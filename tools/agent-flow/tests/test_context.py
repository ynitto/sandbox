"""agent-flow の単体テスト — プロジェクト文脈スナップショット（案 H・安定プレフィックス化）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class ContextSnapshotTests(unittest.TestCase):
    """プロジェクト文脈スナップショット（案 H・--context-file）。
    instructions.py の agent-instructions スナップショットと同型（マーカー・冪等・
    終端後は書かない）だが、置き場所は agent-project が渡すファイルパス。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-context-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _context_file(self, text):
        path = _os.path.join(self.tmp, "context.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_render_context_block_wraps_with_marker(self):
        block = kf.render_context_block("charter の内容")
        self.assertTrue(block.startswith(kf.AGENT_CONTEXT_MARKER))
        self.assertIn("charter の内容", block)
        self.assertEqual(kf.render_context_block(""), "")
        self.assertEqual(kf.render_context_block("   "), "")

    def test_snapshot_writes_meta_and_is_idempotent(self):
        path = self._context_file("プロジェクト文脈テキスト")
        bus = kf.Bus(self.tmp, "run-ctx")
        bus.ensure_run("元要求")
        self.assertTrue(bus.snapshot_context(path))
        meta = bus.run_meta("run-ctx")
        self.assertIn("プロジェクト文脈テキスト", meta["context"]["text"])
        self.assertTrue(meta["context"]["text"].startswith(kf.AGENT_CONTEXT_MARKER))
        # 冪等: 既にスナップショット済みなら再書き込みしない
        self.assertFalse(bus.snapshot_context(path))

    def test_snapshot_skips_when_no_path_or_missing_file(self):
        bus = kf.Bus(self.tmp, "run-nopath")
        bus.ensure_run("req")
        self.assertFalse(bus.snapshot_context(None))
        self.assertFalse(bus.snapshot_context(""))
        self.assertFalse(bus.snapshot_context(_os.path.join(self.tmp, "nope.txt")))
        self.assertNotIn("context", bus.run_meta("run-nopath"))

    def test_snapshot_skips_when_file_is_empty(self):
        path = self._context_file("   ")
        bus = kf.Bus(self.tmp, "run-empty")
        bus.ensure_run("req")
        self.assertFalse(bus.snapshot_context(path))

    def test_run_context_text_reads_the_snapshot(self):
        path = self._context_file("スナップショット済みテキスト")
        bus = kf.Bus(self.tmp, "run-read")
        bus.ensure_run("req")
        bus.snapshot_context(path)
        self.assertIn("スナップショット済みテキスト", kf.run_context_text(bus))

    def test_run_context_text_is_empty_without_snapshot(self):
        bus = kf.Bus(self.tmp, "run-none")
        bus.ensure_run("req")
        self.assertEqual(kf.run_context_text(bus), "")


class KnowledgePassthroughTests(unittest.TestCase):
    """Phase 3: --knowledge-file を meta.knowledge へ素通し（解釈しない）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-knowledge-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _knowledge_file(self, obj):
        path = _os.path.join(self.tmp, "knowledge.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def test_snapshot_writes_meta_without_interpreting(self):
        payload = {"contract_version": 1, "rules_hash": "sha256:abc",
                   "skills": [{"name": "flow-planner", "role": "planner"}]}
        path = self._knowledge_file(payload)
        bus = kf.Bus(self.tmp, "run-kn")
        bus.ensure_run("req")
        self.assertTrue(bus.snapshot_knowledge(path))
        meta = bus.run_meta("run-kn")
        self.assertEqual(meta["knowledge"], payload)
        # 冪等
        self.assertFalse(bus.snapshot_knowledge(path))

    def test_snapshot_skips_bad_or_empty(self):
        bus = kf.Bus(self.tmp, "run-bad")
        bus.ensure_run("req")
        self.assertFalse(bus.snapshot_knowledge(None))
        self.assertFalse(bus.snapshot_knowledge(_os.path.join(self.tmp, "nope.json")))
        bad = _os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not-json")
        self.assertFalse(bus.snapshot_knowledge(bad))
        self.assertNotIn("knowledge", bus.run_meta("run-bad"))


class ReadAllocationTests(unittest.TestCase):
    def test_prompt_and_report_expose_outside_reads(self):
        allocation = [{"path": "src/a.py", "range": "10-20", "reason": "変更点"}]
        note = kf.render_read_allocation(allocation)
        self.assertIn("src/a.py (10-20)", note)
        text, report = kf.extract_read_report(
            'done\n<!-- context-read-report {"used":["src/a.py","src/b.py"],"extra":[]} -->',
            allocation)
        self.assertEqual(text, "done")
        self.assertEqual(report["outside_reads"], 1)
        self.assertFalse(report["hit"])

    def test_planner_allocation_survives_normalization(self):
        node = kf._coerce_tasks([{"id": "t1", "read_allocation": [
            {"path": "src/a.py", "reason": "entry"}]}])[0]
        self.assertEqual(node["read_allocation"][0]["path"], "src/a.py")


class ContinueAgentContextTests(unittest.TestCase):
    """evaluator（continue_agent）: context の前置とオプトイン不変。"""

    def test_prepends_context_when_given(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose=""):
            captured["prompt"] = prompt
            return json.dumps({"decision": "done", "reason": "ok", "new_tasks": []})

        nodes = {"t1": {"goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.continue_agent("要求本文", nodes, results, 0, context="CTX-STABLE-TEXT")
        self.assertTrue(captured["prompt"].startswith("CTX-STABLE-TEXT"))
        self.assertIn("要求本文", captured["prompt"])

    def test_without_context_is_byte_identical_to_before(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose=""):
            captured["prompt"] = prompt
            return json.dumps({"decision": "done", "reason": "ok", "new_tasks": []})

        nodes = {"t1": {"goal": "g", "deps": [], "kind": "work"}}
        results = {"t1": {"status": "done", "output": "ok"}}
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.continue_agent("要求本文", nodes, results, 0)
        without_context = captured["prompt"]
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.continue_agent("要求本文", nodes, results, 0, context="")
        self.assertEqual(without_context, captured["prompt"])
        self.assertNotIn("CTX-STABLE-TEXT", without_context)


class PlanStrategyAgentContextTests(unittest.TestCase):
    """planner（plan_strategy_agent）: context の前置とオプトイン不変。"""

    def test_prepends_context_when_given(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose=""):
            captured["prompt"] = prompt
            return json.dumps({"patterns": ["fan-out-and-synthesize"], "parallelism": 2,
                               "reason": "r", "tasks": [{"id": "t1", "goal": "g", "deps": []}]})

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.plan_strategy_agent("要求本文", None, context="CTX-STABLE-TEXT")
        self.assertTrue(captured["prompt"].startswith("CTX-STABLE-TEXT"))
        self.assertIn("要求本文", captured["prompt"])

    def test_without_context_is_byte_identical_to_before(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose=""):
            captured["prompt"] = prompt
            return json.dumps({"patterns": ["fan-out-and-synthesize"], "parallelism": 2,
                               "reason": "r", "tasks": [{"id": "t1", "goal": "g", "deps": []}]})

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.plan_strategy_agent("要求本文", None)
        without_context = captured["prompt"]
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.plan_strategy_agent("要求本文", None, context="")
        self.assertEqual(without_context, captured["prompt"])


if __name__ == "__main__":
    unittest.main()
