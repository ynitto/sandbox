"""codd_gate_base の単体テスト（標準ライブラリ unittest）。

`resolve_base_rev` は本体からは呼ばれない「明示 import 専用 API」なので、契約はテストでしか
固定できない。優先順位（KIRO_BASE_REV → base ブランチ → HEAD~1）と、本モジュールが本体の
フック契約を満たさない＝自動では掴まれないことを押さえる。

    python -m unittest discover -s tools/agent-project/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codd_gate_base as base


class TestResolveBaseRev(unittest.TestCase):
    def test_env_wins_over_branch(self):
        self.assertEqual(
            base.resolve_base_rev("main", env={"KIRO_BASE_REV": "abc1234"}), "abc1234")

    def test_branch_used_when_env_absent(self):
        self.assertEqual(base.resolve_base_rev("main", env={}), "main")

    def test_blank_env_falls_through_to_branch(self):
        # 本体が baseline を取れず空文字を注入した場合（`--base ""` で codd-gate が死ぬ穴）。
        self.assertEqual(
            base.resolve_base_rev("main", env={"KIRO_BASE_REV": "  "}), "main")

    def test_fallback_when_nothing_known(self):
        self.assertEqual(base.resolve_base_rev(None, env={}), base.FALLBACK_BASE_REV)
        self.assertEqual(base.resolve_base_rev("  ", env={}), base.FALLBACK_BASE_REV)

    def test_env_defaults_to_os_environ(self):
        import os
        prev = os.environ.get("KIRO_BASE_REV")
        os.environ["KIRO_BASE_REV"] = "deadbee"
        try:
            self.assertEqual(base.resolve_base_rev("main"), "deadbee")
        finally:
            if prev is None:
                os.environ.pop("KIRO_BASE_REV", None)
            else:
                os.environ["KIRO_BASE_REV"] = prev


class TestNotAutoWired(unittest.TestCase):
    def test_module_does_not_satisfy_any_hook_capability(self):
        # 本モジュールは明示 import でしか使わない。フック契約の属性を生やすと sibling 走査で
        # 拾われ、「設定なしで繋がる」経路が戻るため、持たないことを固定する。
        # 契約表は `agent_project/hooks.py` の断片から読む（単体 import できない断片の読み方は
        # test_codd_gate_wiring.py に1つだけ置いてあるので再実装しない）。
        from test_codd_gate_wiring import _load_hooks_fragment
        for required in _load_hooks_fragment().HOOK_CAPABILITIES.values():
            for attr in required:
                self.assertFalse(hasattr(base, attr), attr)


if __name__ == "__main__":
    unittest.main()
