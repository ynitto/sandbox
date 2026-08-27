"""ハーネスが**単独で立っている**こと——移植のゴールそのものを縛る。

## 経緯（3 段）

1. 本文は `agent_loop/` の exec 合成断片で、agent-loop のデーモンと tmux 抜きには
   呼べなかった。まず逐語コピーを置き、AST パリティテストでずれを縛った。
2. 写しを畳み、本文を `_toolloop_body.py` というデータファイルにして agent_loop と
   agentcore の**両方が exec** した。import 委譲にできなかったのは、agent-loop の
   テストが共有名前空間を差し替えていたから。
3. そのテストをこのディレクトリへ移し、agent_loop 側を**ただの委譲**にした。
   本文はこの 2 モジュールにしか無く、exec もデータファイルも要らない。

ここが見るのは agentcore 側の不変条件だけである（agent-loop との継ぎ目は
`tools/agent-loop/test/test_harness_delegation.py`）。振る舞いそのものは
`test_harness_statemachine.py` / `test_harness_control_retry.py` /
`test_harness_agent_timeout.py` が見る。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

HARNESS = Path(__file__).resolve().parents[1] / "harness"
NAMES = ("toolloop", "statemachine")


class TheHarnessStandsAloneTests(unittest.TestCase):
    def test_the_modules_import_without_agent_loop(self):
        """agent-loop のデーモンも tmux も無しに呼べること（移植の目的）。"""
        from agentcore.harness import statemachine, toolloop
        self.assertTrue(callable(toolloop.run_prompt))
        self.assertTrue(callable(statemachine.run_statemachine))
        self.assertNotIn("agent_loop", sys.modules,
                         "ハーネスが agent-loop を引きずり込んでいる")

    def test_the_body_lives_in_these_modules(self):
        """本文はここにある。薄いと、また別の場所へ本文が逃げたということ。"""
        for name in NAMES:
            source = (HARNESS / f"{name}.py").read_text(encoding="utf-8")
            self.assertGreater(len(source.splitlines()), 500,
                               f"{name}.py が薄すぎる（本文がここに無い）")

    def test_no_body_data_file_comes_back(self):
        """本文をデータファイルにして exec で配る形（段2）へ戻っていないこと。

        戻ると traceback も `inspect.getsource` も遠回りになり、「どこを直せばいいか」が
        1 段ぼやける。委譲で足りる以上、その遠回りを買う理由はもう無い。
        """
        self.assertEqual(sorted(p.name for p in HARNESS.glob("_*_body.py")), [])
        for name in NAMES:
            source = (HARNESS / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn("exec(compile(", source, f"{name}.py が本文を exec している")

    def test_tracebacks_point_at_the_real_file(self):
        from agentcore.harness import toolloop
        path, line = inspect.getsourcefile(toolloop.run_prompt), \
            inspect.getsourcelines(toolloop.run_prompt)[1]
        self.assertEqual(Path(path).resolve(), HARNESS / "toolloop.py")
        self.assertGreater(line, 1)

    def test_the_layer_switch_is_the_single_branch_point(self):
        """層 2 / 層 3 の分岐が `headless_autonomy` の 1 点であること（設計 §5.3）。

        コマンド行を渡すか消費するかは**別の宣言**（`slash_native`）で、以前はこの層を
        代理に使っていた（設計 2026-08-27 §3.2）。2 つが同じ 1 行に乗っていないことを見る。
        """
        from agentcore.harness import toolloop
        source = inspect.getsource(toolloop.run_prompt)
        for token in ("headless_autonomy", "tool-loop", "run_cli_loop", "run_goal",
                      "slash_native"):
            self.assertIn(token, source)
        runner_branch = [line for line in source.splitlines()
                         if "headless_autonomy" in line and line.strip().startswith("if ")]
        self.assertEqual(len(runner_branch), 1, "runner の分岐は 1 点")
        self.assertNotIn("slash_native", runner_branch[0], "スラッシュの宣言を層に相乗りさせない")


class TheSeamsAreOffByDefaultTests(unittest.TestCase):
    """host が差し込むまで、記帳も control 解決も起こらない。"""

    def test_budget_recording_is_off_until_a_host_wires_it(self):
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

    def test_control_resolution_is_none_by_default(self):
        from agentcore.harness import _borrowed
        self.assertIsNone(_borrowed._no_control_policy(""))


if __name__ == "__main__":
    unittest.main()
