"""トリガー評価器（`.github/skills/skill-evaluator/scripts/eval_trigger.py`）の単体テスト。

固定するのは 2 つだけ: `--check-env` が `--skill-path` なしで通ること、summary が
正例・負例に分かれて合計と一致すること。**件数は固定しない**（eval.json を直した日に
落ちるため）。評価は簡易モード（`--heuristic`。LLM を呼ばない）で走らせる。
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / ".github" / "skills" / "skill-evaluator" / "scripts" / "eval_trigger.py"
SKILL = ROOT / ".github" / "skills" / "agent-project"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8")


class EvalTriggerTest(unittest.TestCase):
    def test_check_env_without_skill_path(self):
        self.assertEqual(run("--check-env").returncode, 0)

    def test_missing_skill_path_is_usage_error(self):
        self.assertEqual(run("--heuristic").returncode, 2)

    def test_summary_splits_positive_and_negative(self):
        # 終了コードは満点でないと 1 になるので見ない（出力だけ読む）
        proc = run("--skill-path", str(SKILL),
                   "--eval-set", str(SKILL / "eval.json"), "--heuristic")
        out = json.loads(proc.stdout)
        s = out["summary"]
        self.assertEqual(s["positive"]["total"] + s["negative"]["total"], s["total"])
        self.assertEqual(s["positive"]["passed"] + s["negative"]["passed"], s["passed"])
        for r in out["results"]:
            self.assertIsInstance(r["target_rank"], int)


if __name__ == "__main__":
    unittest.main()
