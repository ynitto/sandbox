"""split の分解粒度の tier 対応 — カスタムフロー・map-reduce の動的 fan-out への補償（G1）。

fan-out の細かさは split の出力要素数が決め、展開（_expand_splits）自体は LLM を通らない。
basic では split のプロンプトへ「1 手順で終わる大きさまで割る」指示を後置し、
出力契約（JSON 配列のみ）は変えない。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403


class TierSplitDirectiveTests(unittest.TestCase):
    def test_directive_only_for_basic(self):
        self.assertIn("basic", kf.tier_split_directive("basic"))
        self.assertEqual(kf.tier_split_directive(""), "")
        self.assertEqual(kf.tier_split_directive("large"), "")
        self.assertEqual(kf.tier_split_directive(None), "")

    def test_directive_restates_list_contract(self):
        # プロンプト末尾への後置なので、指示文の中でも出力契約（配列のみ・説明文なし）を
        # もう一度言う——ここが崩れると _expand_splits が展開されず run が空振りする。
        note = kf.tier_split_directive("basic")
        self.assertIn("JSON 配列", note)
        self.assertIn("説明文", note)


class TierSplitPromptTests(unittest.TestCase):
    def _capture(self, kind="split", agent=None, control_tier=""):
        seen = {}

        def fake_agent(prompt, model, purpose="", **_kw):
            seen["prompt"] = prompt
            return '["a", "b"]'

        with mock.patch.object(kf, "run_agent", fake_agent), \
             mock.patch.object(kf, "flow_tier", return_value=control_tier):
            kf.execute_agent(kind, "対象を分解", {}, None, agent=agent)
        return seen["prompt"]

    def test_basic_workload_injects_directive(self):
        prompt = self._capture(control_tier="basic")
        self.assertIn(kf.tier_split_directive("basic"), prompt)

    def test_other_tiers_leave_prompt_unchanged(self):
        base = self._capture(control_tier="")
        self.assertNotIn("実行ティア", base)
        self.assertEqual(self._capture(control_tier="large"), base)

    def test_node_pinned_tier_overrides_workload(self):
        # ノード固定 tier は workload 宣言より優先（_method_context と同じ解決順）
        prompt = self._capture(agent={"tier": "large"}, control_tier="basic")
        self.assertNotIn("実行ティア", prompt)
        prompt = self._capture(agent={"tier": "basic"}, control_tier="")
        self.assertIn(kf.tier_split_directive("basic"), prompt)

    def test_directive_keeps_list_contract_in_prompt(self):
        # 指示が入っても split の出力契約（配列のみ）の記述は残る
        prompt = self._capture(control_tier="basic")
        self.assertIn("JSON 配列", prompt)
        self.assertIn("説明文は付けず配列だけを返すこと", prompt)

    def test_non_split_kinds_do_not_get_directive(self):
        # 補償は split（動的 fan-out の粒度を決める役割）だけに効かせる
        for kind in ("work", "map", "reduce", "classify"):
            self.assertNotIn("実行ティア", self._capture(kind=kind, control_tier="basic"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
