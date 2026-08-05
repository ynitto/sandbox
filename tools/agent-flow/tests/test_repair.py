"""agent-flow の単体テスト — 差分修復リトライ（案 B-1・repair_brief）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


def _args(**over):
    base = {"repair_retry": True, "repair_excerpt_bytes": 4000}
    base.update(over)
    return types.SimpleNamespace(**base)


class RepairBriefTests(unittest.TestCase):
    """work.py の repair_brief()。retry ノードの `replaces` からバス上の実状態を
    決定的に組み立てる（stub 経路・evaluator 経路の両方が通る 1 実装）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-repair-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(self.tmp, "run1")
        self.bus.ensure_run("req")

    def _graph(self, nodes):
        self.bus.write_graph({"nodes": nodes, "iteration": 0})

    def test_off_by_default_returns_none(self):
        """オプトイン: repair_retry 未指定（既定 False）では常に None。"""
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        self.bus.write_result("t1", "w1", "failed", "だめ")
        node = {"id": "t1r", "goal": "g", "deps": [], "kind": "work", "replaces": "t1"}
        self.assertIsNone(kf.repair_brief(self.bus, node, _args(repair_retry=False)))

    def test_no_replaces_is_not_a_retry_node(self):
        node = {"id": "t1", "goal": "g", "deps": [], "kind": "work"}
        self.assertIsNone(kf.repair_brief(self.bus, node, _args()))

    def test_no_previous_result_fails_safe(self):
        """前回結果が無い（壊れている／未書込）ならフェイルセーフで None（例外を投げない）。"""
        node = {"id": "t1r", "goal": "g", "deps": [], "kind": "work", "replaces": "missing"}
        self.assertIsNone(kf.repair_brief(self.bus, node, _args()))

    def test_gathers_previous_output_and_verify_issues(self):
        """verify ノード（deps に旧ノードを含む）の issues と、前回の成果（有界抜粋）を集める。"""
        self._graph({
            "t1": {"goal": "実装して", "deps": [], "kind": "work"},
            "v1": {"goal": "検証", "deps": ["t1"], "kind": "verify"},
        })
        self.bus.write_result("t1", "w1", "done", "実装した内容の全文")
        self.bus.write_result("v1", "w2", "done", "verify=fail",
                              data={"ok": False, "issues": ["境界値が違う", "テスト1件失敗"]})
        node = {"id": "t1-r1", "goal": "実装して", "deps": [], "kind": "work", "replaces": "t1"}
        brief = kf.repair_brief(self.bus, node, _args())
        self.assertEqual(brief["of"], "t1")
        self.assertEqual(brief["output"], "実装した内容の全文")
        self.assertEqual(brief["issues"], ["境界値が違う", "テスト1件失敗"])
        self.assertFalse(brief["delivered"])
        self.assertIsNone(brief["artifact_dir"])

    def test_delivered_reflects_prior_commit(self):
        """前回の成果に delivery（作業ブランチへの反映）があれば delivered=True。"""
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        self.bus.write_result("t1", "w1", "done", "できた",
                              data={"delivery": {"branch": "af/run1", "sha": "abc123"}})
        node = {"id": "t1-r1", "goal": "g", "deps": [], "kind": "work", "replaces": "t1"}
        brief = kf.repair_brief(self.bus, node, _args())
        self.assertTrue(brief["delivered"])

    def test_artifact_dir_only_when_files_exist(self):
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        self.bus.write_result("t1", "w1", "done", "できた")
        node = {"id": "t1-r1", "goal": "g", "deps": [], "kind": "work", "replaces": "t1"}
        self.assertIsNone(kf.repair_brief(self.bus, node, _args())["artifact_dir"])
        art_dir = self.bus.ensure_artifact_dir("t1")
        with open(_os.path.join(art_dir, "out.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        brief = kf.repair_brief(self.bus, node, _args())
        self.assertEqual(brief["artifact_dir"], self.bus.node_artifact_dir("t1"))

    def test_agent_error_tag_is_folded_into_issues(self):
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        self.bus.write_result("t1", "w1", "failed", "[agent-error:content] 実行エラー: x")
        node = {"id": "t1-r1", "goal": "g", "deps": [], "kind": "work", "replaces": "t1"}
        brief = kf.repair_brief(self.bus, node, _args())
        self.assertIn("前回の失敗分類: content", brief["issues"])

    def test_retry_depth_two_or_more_falls_back_to_full_rebuild(self):
        """修復は同一系統 1 回だけ。retries>=2 は従来の全作り直し（None）へ戻る。"""
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        self.bus.write_result("t1", "w1", "failed", "だめ")
        node_r1 = {"id": "t1-r1", "goal": "g", "deps": [], "kind": "work",
                   "replaces": "t1", "retries": 1}
        self.assertIsNotNone(kf.repair_brief(self.bus, node_r1, _args()))
        node_r2 = {"id": "t1-r2", "goal": "g", "deps": [], "kind": "work",
                   "replaces": "t1", "retries": 2}
        self.assertIsNone(kf.repair_brief(self.bus, node_r2, _args()))

    def test_excerpt_is_byte_bounded_and_utf8_safe(self):
        """切り詰めは決定的・UTF-8 のバイト境界で安全（マルチバイト文字の途中で壊さない）。"""
        self._graph({"t1": {"goal": "g", "deps": [], "kind": "work"}})
        long_text = "あ" * 5000   # 1 文字 3 バイト
        self.bus.write_result("t1", "w1", "done", long_text)
        node = {"id": "t1-r1", "goal": "g", "deps": [], "kind": "work", "replaces": "t1"}
        brief = kf.repair_brief(self.bus, node, _args(repair_excerpt_bytes=100))
        self.assertLessEqual(len(brief["output"].encode("utf-8")), 100 + len("…".encode("utf-8")))
        # 決定的: 同じ入力なら同じ出力
        brief2 = kf.repair_brief(self.bus, node, _args(repair_excerpt_bytes=100))
        self.assertEqual(brief["output"], brief2["output"])

    def test_stub_and_evaluator_paths_produce_the_same_brief(self):
        """stub 経路（continuation._continue_stub）と evaluator 経路のどちらから生まれた
        retry ノードでも、repair_brief は同じ材料を返す（1 実装であることの確認）。"""
        self._graph({
            "t1": {"goal": "実装して", "deps": [], "kind": "work"},
            "v1": {"goal": "検証", "deps": ["t1"], "kind": "verify"},
        })
        self.bus.write_result("t1", "w1", "done", "前回の成果")
        self.bus.write_result("v1", "w2", "done", "verify=fail", data={"ok": False, "issues": ["直せ"]})
        stub_node = {"id": "t1-r1", "goal": "実装して", "deps": [], "kind": "work", "replaces": "t1"}
        evaluator_node = {"id": "fix-t1", "goal": "実装して（修正）", "deps": [], "kind": "work",
                          "replaces": "t1"}
        b1 = kf.repair_brief(self.bus, stub_node, _args())
        b2 = kf.repair_brief(self.bus, evaluator_node, _args())
        self.assertEqual(b1["of"], b2["of"])
        self.assertEqual(b1["output"], b2["output"])
        self.assertEqual(b1["issues"], b2["issues"])


if __name__ == "__main__":
    unittest.main()
