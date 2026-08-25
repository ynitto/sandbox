"""ハーネスの本文が 1 つであること——写しが復活していないことを縛る。

## 経緯

段1（ポーティング）では `agentcore.harness` が `agent_loop` の断片の**逐語コピー**を持ち、
AST でずれを突き合わせていた。段2（移行）で写しを畳み、いまは本文が
`_toolloop_body.py` / `_statemachine_body.py` の**1 か所だけ**にある。

2 つの実行系が同じ本文を exec する:

- :mod:`agentcore.harness.toolloop` … 前置き（stdlib と継ぎ目）を用意して自分の名前空間へ
- ``agent_loop/toolloop.py`` … agent_loop の共有名前空間へ（従来どおりの合成）

## なぜ import による委譲にしなかったか

agent_loop のテストは `mock.patch.object(agent_loop, "_tl_run_agent")` のように
**共有名前空間を差し替える**（57 箇所）。通常の import で委譲すると関数の ``__globals__`` が
agentcore 側になるため、その差し替えが黙って効かなくなる。本文をデータとして共有すれば、
写しを持たずに agent_loop の意味論をそのまま保てる——このテストがその不変条件を縛る。
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

HARNESS = _REPO / "tools" / "agent-tools" / "agentcore" / "agentcore" / "harness"
FRAGMENTS = _REPO / "tools" / "agent-loop" / "agent_loop"
NAMES = ("toolloop", "statemachine")

# 本文が 1 か所であることの目安。写しが戻れば断片がこの桁になる。
FRAGMENT_MAX_LINES = 60


class TheBodyLivesInExactlyOnePlaceTests(unittest.TestCase):
    def test_the_body_files_exist(self):
        for name in NAMES:
            body = HARNESS / f"_{name}_body.py"
            self.assertTrue(body.is_file(), f"本文が無い: {body}")
            self.assertGreater(len(body.read_text(encoding="utf-8").splitlines()), 500,
                               f"{name} の本文が薄すぎる（分割を間違えている）")

    def test_the_agent_loop_fragment_no_longer_carries_a_copy(self):
        """断片は本文を持たない。ここが太ると写しが戻ったということ。"""
        for name in NAMES:
            fragment = FRAGMENTS / f"{name}.py"
            lines = fragment.read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(
                len(lines), FRAGMENT_MAX_LINES,
                f"agent_loop/{name}.py が {len(lines)} 行ある。本文は "
                f"agentcore/harness/_{name}_body.py の 1 か所だけに置くこと")

    def test_both_sides_load_the_same_body(self):
        """agent_loop と agentcore が**同じファイル名**を読んでいること。"""
        for name in NAMES:
            data = f'"_{name}_body.py"'
            for path in (FRAGMENTS / f"{name}.py", HARNESS / f"{name}.py"):
                self.assertIn(data, path.read_text(encoding="utf-8"),
                              f"{path} が共有本文を読んでいない")

    def test_the_body_is_not_importable_on_its_own(self):
        """前置きなしでは動かない＝データファイルであることの確認。

        単体で import できてしまうなら、それは本文が自分で前置きを持っている（＝
        どちらかの実行系向けに寄っている）ということなので、共有の前提が崩れている。
        """
        for name in NAMES:
            source = (HARNESS / f"_{name}_body.py").read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            self.assertEqual(imports, [], f"_{name}_body.py が自前の import を持っている")


class SharedNamespaceSemanticsTests(unittest.TestCase):
    """agent_loop 側の意味論——共有名前空間への差し替えが効くこと。"""

    def setUp(self):
        sys.path.insert(0, str(_REPO / "tools" / "agent-loop"))
        self.addCleanup(lambda: sys.path.remove(str(_REPO / "tools" / "agent-loop")))

    def test_the_composed_functions_run_in_agent_loops_namespace(self):
        """`mock.patch.object(agent_loop, ...)` が効く条件そのもの。

        import 委譲へ変えるとここが agentcore 側を指し、agent-loop の 57 箇所の
        差し替えが黙って無効になる。
        """
        import agent_loop as al
        for fn in (al._tl_run_agent, al.run_prompt, al.run_statemachine):
            self.assertIs(fn.__globals__, vars(al),
                          f"{fn.__name__} が共有名前空間で動いていない")

    def test_agent_loop_wires_its_own_ledger_and_control(self):
        """継ぎ目のフックに agent-loop の実装が差し込まれていること。

        差し込まれないと、agentcore の既定（記帳しない / control 解決しない）のまま
        agent-loop が動いてしまい、台帳が静かに空になる。
        """
        import agent_loop  # noqa: F401  (合成が set_hooks を呼ぶ)
        from agentcore.harness import _borrowed
        self.assertIsNot(_borrowed.node_budget_record, _borrowed._noop_budget_record)
        self.assertIsNot(_borrowed.control_policy_decision, _borrowed._no_control_policy)


class PortedHarnessIsImportableTests(unittest.TestCase):
    """agentcore 側の意味論——単体 import できること（移植の目的そのもの）。"""

    def test_the_modules_import_without_agent_loop(self):
        from agentcore.harness import statemachine, toolloop
        self.assertTrue(callable(toolloop.run_prompt))
        self.assertTrue(callable(statemachine.run_statemachine))

    def test_the_ported_functions_run_in_their_own_namespace(self):
        """2 つの実行系は本文を共有するが、名前空間は別（互いのパッチは干渉しない）。"""
        from agentcore.harness import toolloop
        self.assertIs(toolloop.run_prompt.__globals__, vars(toolloop))

    def test_the_layer_switch_is_the_single_branch_point(self):
        """層 2 / 層 3 の分岐が `headless_autonomy` の 1 点であること（設計 §5.3）。"""
        import inspect

        from agentcore.harness import toolloop
        source = inspect.getsource(toolloop.run_prompt)
        for token in ("headless_autonomy", "tool-loop", "run_cli_loop", "run_goal"):
            self.assertIn(token, source)

    def test_budget_recording_is_off_until_a_host_wires_it(self):
        """agentcore 単体の既定は記帳しない。どこかの台帳へ黙って書くより書かない。"""
        from agentcore import harness
        from agentcore.harness import _borrowed
        saved = _borrowed.node_budget_record
        seen = []
        try:
            _borrowed.node_budget_record = _borrowed._noop_budget_record
            self.assertIsNone(_borrowed.node_budget_record(1.0, agent_cli="x"))
            harness.set_hooks(node_budget_record=lambda *a, **k: seen.append((a, k)))
            _borrowed.node_budget_record(1.0, agent_cli="x")
            self.assertEqual(len(seen), 1, "set_hooks で差し込んだ記帳が呼ばれていない")
        finally:
            _borrowed.node_budget_record = saved


if __name__ == "__main__":
    unittest.main()
