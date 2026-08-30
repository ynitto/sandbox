"""LLM 出力からの JSON 抽出（agentcore.llmjson）— 寛容さの規則は 1 実装で持つ。

道具ループを回すモデルは、成果物をフェンスや行内引用に入れて**その前後に作業報告を書く**。
素朴な「最初の `[` から最後の `]`」は後置きの散文を巻き込んで壊れる——実測 2026-08-30 の
map は不合格 4/5 のうち 3 本が**正しい JSON をフェンスの中に持ったまま** unparsable で
落ちていた（本番も同じ抽出を使うので、engine 側でも data=None になっていた）。

    python3 -m pytest tools/agent-tools/agentcore/tests/test_llmjson.py
"""
import unittest

from agentcore import llmjson


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(llmjson.extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(llmjson.extract_json('["a", "b"]'), ["a", "b"])

    def test_fence_survives_trailing_prose_with_brackets(self):
        """フェンスの後ろに角括弧を含む散文があっても壊れない（map の実測の形）。"""
        text = ('成果物を提示します。\n\n```json\n["概要", "手順", "補足"]\n```\n\n'
                '**検証**: 形式は `[ "# 見出し1", "# 見出し2", ... ]` に合致している。\n'
                'TASK_COMPLETE')
        self.assertEqual(llmjson.extract_json(text), ["概要", "手順", "補足"])

    def test_last_successful_fence_wins(self):
        """途中経過が先・最終成果が最後に来る（道具ループの順序）。"""
        text = ('一度目の試行:\n```json\n{"draft": true}\n```\n'
                '修正しました:\n```json\n{"final": true}\n```')
        self.assertEqual(llmjson.extract_json(text), {"final": True})

    def test_non_json_fence_is_skipped(self):
        text = ('実行したコマンド:\n```bash\ngrep -E "^#" ITEM-07.md\n```\n'
                '結果:\n```json\n["概要"]\n```')
        self.assertEqual(llmjson.extract_json(text), ["概要"])

    def test_inline_backticks_when_there_is_no_fence(self):
        """フェンスを使わず本文へ埋める癖。周りの散文にも角括弧が出る。"""
        text = ('最終実行結果が `["概要", "手順", "補足"]` となり、要求を満たしています。\n'
                '検証: 見出しレベル 1 (`# 概要`) → "概要" のように整形した [完了]。')
        self.assertEqual(llmjson.extract_json(text), ["概要", "手順", "補足"])

    def test_bare_slice_still_works_without_fences(self):
        """フェンスも行内引用も無い出力は従来どおり切り出しで拾う。"""
        self.assertEqual(llmjson.extract_json('前置き {"a": 1} 後置き'), {"a": 1})

    def test_trailing_control_envelope_does_not_hide_the_payload(self):
        """ツールループ CLI が後ろへ足す `{"ok": false, ...}` は payload ではない。

        実測 2026-08-30: amigos の conductor / acceptance / role-actions は、モデルが正しい
        JSON を返しているのに、この封筒が後続して「最初の { から最後の }」が壊れ全滅していた。
        """
        text = ('{"add": [], "prune": [], "reason": "編成は足りている"}\n\n'
                '{"ok": false, "issues": ["規約どおりの完了宣言を出せず打ち切りました"]}')
        self.assertEqual(llmjson.extract_json(text)["reason"], "編成は足りている")

    def test_control_envelope_alone_is_still_returned(self):
        """封筒しか無いときは封筒を返す（verify の正規化はこれを読む）。"""
        self.assertEqual(llmjson.extract_json('{"ok": true, "issues": []}'),
                         {"ok": True, "issues": []})

    def test_nested_arrays_are_not_mistaken_for_the_payload(self):
        """読めた値の中は走査しない（`issues: [...]` を成果と取り違えない）。"""
        text = '雑談 {"actions": [{"type": "note"}]} 続き'
        self.assertEqual(llmjson.extract_json(text), {"actions": [{"type": "note"}]})

    def test_no_json_raises_with_the_caller_label(self):
        with self.assertRaises(ValueError) as ctx:
            llmjson.extract_json("成果報告のみでデータがありません。", what="planner 出力")
        self.assertIn("planner 出力", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
