"""移植した harness が、元の agent_loop 断片と 1 行もずれていないことを縛る。

`agentcore.harness.toolloop` / `.statemachine` は `agent_loop/toolloop.py` /
`agent_loop/statemachine.py` の**移植**で、元は消していない（移行ではなくポーティング）。
agent-loop は従来どおり自分の断片を使うので、いまこの 2 つは**意図的な写し**である。

複製そのものは禁じない——「まず移植して、寄せるのは後」という段取りから来る意図的な状態
だからだ。危ないのは**片方だけ直しても両方のテストが緑のまま**であることで、症状は
「agent-loop 経由なら通るのに agent-herd から回すと落ちる（またはその逆）」という、
どちらのコードを読んでも理由が見えない形で出る。だから一致を機械に突き合わせさせる
（`hostenv` を 1 実装へ畳む前に `test_adapter_env_parity.py` がやっていたのと同じ流儀）。

比較は AST で行い、docstring とコメントは無視する。**説明は各ファイルの文脈で違ってよく、
揃っていなければならないのは振る舞いだけ**だからである。

移植の継ぎ目（`_borrowed` から供給する借用名）は比較から除く——そこが移植で唯一変えた
ところで、揃っていたら移植になっていない。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[5]

PORTED = _REPO / "tools" / "agent-tools" / "agentcore" / "agentcore" / "harness"
ORIGIN = _REPO / "tools" / "agent-loop" / "agent_loop"

# 移植で供給し直した借用名。ここだけは一致しなくてよい（＝移植の継ぎ目）。
SEAM = {
    "toolloop": {"agent_home_subdir", "_import_agentcli", "_node_budget_record"},
    "statemachine": {"_control_policy_decision"},
}


def _strip_docstring(body: "list") -> "list":
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


def _definitions(path: Path) -> "dict[str, str]":
    """top-level の関数・クラス・代入を {名前: 正規化ソース} で返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "dict[str, str]" = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            clone = ast.Module(body=_strip_docstring(list(node.body)), type_ignores=[])
            out[node.name] = f"def {node.name}{ast.unparse(node.args)}\n" + ast.unparse(clone)
        elif isinstance(node, ast.ClassDef):
            clone = ast.Module(body=_strip_docstring(list(node.body)), type_ignores=[])
            out[node.name] = f"class {node.name}\n" + ast.unparse(clone)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.unparse(node.value)
    return out


class HarnessPortParityTests(unittest.TestCase):
    def test_both_sides_exist(self):
        """どちらかが消えた・移動したことに気づかないまま緑にしない。"""
        for name in ("toolloop", "statemachine"):
            self.assertTrue((PORTED / f"{name}.py").is_file(), f"移植先が無い: {name}")
            self.assertTrue((ORIGIN / f"{name}.py").is_file(),
                            f"元が無い: {name}（移行したなら本テストを畳むこと）")

    def test_the_original_fragments_are_untouched_by_the_port(self):
        """ポーティングであって移行ではない——agent_loop は自分の断片を持ち続ける。

        元を消して委譲へ替えるのは別の判断（移行）で、そのときは agent-loop の全テストを
        合格ゲートにする。本テストはその判断がまだ行われていないことを固定する。
        """
        init = (ORIGIN / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"toolloop"', init, "agent_loop の合成順から toolloop が消えている")
        self.assertIn('"statemachine"', init, "agent_loop の合成順から statemachine が消えている")

    def test_every_definition_matches_the_origin(self):
        for name in ("toolloop", "statemachine"):
            origin = _definitions(ORIGIN / f"{name}.py")
            ported = _definitions(PORTED / f"{name}.py")
            seam = SEAM[name]
            missing = sorted(set(origin) - set(ported))
            self.assertEqual(missing, [], f"{name}: 移植先に無い定義がある（移植漏れ）")
            for key in sorted(origin):
                if key in seam:
                    continue
                self.assertEqual(
                    origin[key], ported[key],
                    f"{name}.{key} が元（agent_loop/{name}.py）とずれています。"
                    "移植先は逐語コピーで、まだ元を消していません。片方だけ直すと"
                    "『agent-loop からは通るのに agent-herd からは落ちる』として出ます。"
                    "両方を揃えるか、移行（元を消して委譲へ）を別の変更として行ってください。")

    def test_the_seam_is_actually_different(self):
        """継ぎ目が元と同じなら、それは移植できていない（借用のまま）ということ。"""
        for name in ("toolloop", "statemachine"):
            origin = _definitions(ORIGIN / f"{name}.py")
            ported = _definitions(PORTED / f"{name}.py")
            for key in SEAM[name]:
                self.assertIn(key, ported, f"{name}: 継ぎ目 {key} が移植先にない")
                self.assertNotIn(key, origin,
                                 f"{name}: {key} は元では借用名（top-level 定義でない）はず")

    def test_comments_may_differ(self):
        """説明は各ファイルの文脈で違ってよい——比較対象が振る舞いだけであることの確認。"""
        origin_src = (ORIGIN / "toolloop.py").read_text(encoding="utf-8")
        ported_src = (PORTED / "toolloop.py").read_text(encoding="utf-8")
        self.assertNotEqual(origin_src, ported_src, "前提が変わった（同一ファイルになっている）")
        self.assertEqual(_definitions(ORIGIN / "toolloop.py")["run_prompt"],
                         _definitions(PORTED / "toolloop.py")["run_prompt"])


class PortedHarnessIsImportableTests(unittest.TestCase):
    """移植の目的そのもの——単体 import できること（元の断片はこれができない）。"""

    def test_the_modules_import_without_agent_loop(self):
        from agentcore.harness import statemachine, toolloop
        self.assertTrue(callable(toolloop.run_prompt))
        self.assertTrue(callable(statemachine.run_statemachine))

    def test_the_layer_switch_is_the_single_branch_point(self):
        """層 2 / 層 3 の分岐が `headless_autonomy` の 1 点であること（設計 §5.3）。"""
        import inspect
        from agentcore.harness import toolloop
        source = inspect.getsource(toolloop.run_prompt)
        self.assertIn("headless_autonomy", source)
        self.assertIn("tool-loop", source)
        self.assertIn("run_cli_loop", source)
        self.assertIn("run_goal", source)

    def test_budget_recording_is_off_until_a_host_wires_it(self):
        """既定は記帳しない。どこかの台帳へ黙って書くより、書かないを既定にする。"""
        from agentcore import harness
        from agentcore.harness import _borrowed
        seen = []
        try:
            self.assertIsNone(_borrowed.node_budget_record(1.0, agent_cli="x"))
            harness.set_hooks(node_budget_record=lambda *a, **k: seen.append((a, k)))
            _borrowed.node_budget_record(1.0, agent_cli="x")
            self.assertEqual(len(seen), 1, "set_hooks で差し込んだ記帳が呼ばれていない")
        finally:
            _borrowed.node_budget_record = _borrowed._noop_budget_record

    def test_control_policy_defaults_to_none(self):
        """None = selection_policy 無し。本体は従来どおり pin / 既定候補で走る。"""
        from agentcore.harness import _borrowed
        self.assertIsNone(_borrowed.control_policy_decision("statemachine"))


if __name__ == "__main__":
    unittest.main()
