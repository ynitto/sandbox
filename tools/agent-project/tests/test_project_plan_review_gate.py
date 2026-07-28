"""plan ゲートの回帰テスト — 分解（plan）は人の明示要求でしか走らない。

かつては「消化可能タスクが無い」「charter が変わった」でも自動分解が走り、proposed だけの
バックログ（人の計画レビュー待ち）を人待ちと見なすガード（_has_project_human_wait）で
近接重複の積み増しを防いでいた。いまは分解の契機が明示要求（replan マーカー）だけになった
ので、そのガード自体が不要になった——ここでは「要求なしでは planner が一切呼ばれない」
という新契約そのものを固定する。
"""
import json
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _drained  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class ProjectPlanGateTest(unittest.TestCase):
    def test_no_request_means_no_plan_even_with_empty_backlog(self):
        # バックログが空（消化可能ゼロ）でも、明示要求が無ければ分解しない。
        # ——人が削除・整理した直後のパスが黙って作り直さないことの保証。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=1)
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return [{"title": "作り直しタスク", "verify": "true"}]

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(calls["n"], 0)
            self.assertEqual(km.load_tasks(cfg.backlog), [])

    def test_proposed_only_backlog_does_not_trigger_plan(self):
        # proposed（計画レビュー待ち）だけのバックログでも分解しない＝人がレビュー中の計画に
        # 近接重複が積み増されない（旧 _has_project_human_wait が守っていた性質の新しい形）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("{flag}", str(d / "never")))
            cfg = cfg_for(d, max_project_cycles=1)
            mkb(d, "T-plan", title="UI backlog proposal", status="proposed", verify="true")
            calls = {"n": 0}

            def planner(ch):
                calls["n"] += 1
                return [{"title": "UI backlog proposal 改", "verify": "true"}]

            km.cmd_project(cfg, planner=planner, runner=lambda c: _drained())
            self.assertEqual(calls["n"], 0)
            titles = [t.title for t in km.load_tasks(cfg.backlog)]
            self.assertEqual(titles, ["UI backlog proposal"])


if __name__ == "__main__":
    unittest.main()
