"""agent-flow の単体テスト — doctor（`_doctor_prompt` の圧縮描画・案 K-1/K-2）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。
設計: docs/plans/2026-08-05-json-prompt-compression-study.md

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）

import argparse  # noqa: E402


class DoctorPromptCompressionTests(unittest.TestCase):
    """案 K-1（常時 compact）/ 案 K-2（table・オプトイン）。内容（キー・値）は不変。"""

    def _signals(self):
        return {"recent": [{"run": "r1", "status": "done"}, {"run": "r2", "status": "failed"}]}

    def test_default_is_compact_json_not_indented(self):
        prompt = kf._doctor_prompt(self._signals(), [])
        block = prompt.split("=== 稼働シグナル", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("  ", block)
        self.assertIn('{"recent":[{"run":"r1","status":"done"},'
                      '{"run":"r2","status":"failed"}]}', prompt)

    def test_table_opt_in_folds_homogeneous_arrays(self):
        default_prompt = kf._doctor_prompt(self._signals(), [])
        table_prompt = kf._doctor_prompt(self._signals(), [], table=True)
        self.assertNotIn("recent[2]{run,status}:", default_prompt)
        self.assertIn("recent[2]{run,status}:", table_prompt)

    def test_diagnose_with_agent_honors_prompt_table_arg(self):
        captured = {}

        def capture(p, m):
            captured["prompt"] = p
            return "[]"
        args = argparse.Namespace(model=None, prompt_table=True)
        kf.diagnose_with_agent(args, self._signals(), [], agent_run=capture)
        self.assertIn("recent[2]{run,status}:", captured["prompt"])

    def test_diagnose_with_agent_defaults_to_compact_without_flag(self):
        captured = {}

        def capture(p, m):
            captured["prompt"] = p
            return "[]"
        args = argparse.Namespace(model=None)
        kf.diagnose_with_agent(args, self._signals(), [], agent_run=capture)
        self.assertNotIn("recent[2]{run,status}:", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
