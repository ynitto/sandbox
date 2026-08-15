"""agent-flow の単体テスト — e2e（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class EndToEndTests(unittest.TestCase):
    def _run_up(self, bus, request, extra=None, timeout=90):
        cmd = [sys.executable, str(SCRIPT), "--bus", bus, "run", request,
               "--workers", "3", "--planner", "stub", "--executor", "stub", "--poll", "0.2"]
        cmd += extra or []
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p

    def _final(self, bus):
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        return kf.read_json(os.path.join(bus, "runs", run_id, "final.json"))

    def test_r9_skill_invocation_standalone_completes_without_daemon_or_git(self):
        """R9 非退行テスト（常駐一本化 実装計画 §0-4・P0 完了条件）: エージェントチャット /
        スキルから `agent-flow run <要求>` を単発起動する経路（常駐体なし・ネットワークなし・
        `--git` 未指定のローカル dir バス）が、この先の常駐一本化のどのフェーズが進んでも
        壊れないことを固定する。CLI 名・サブコマンド名・引数の形も本テストが変われば
        イコール破壊的変更——変えるときは意図して変えたことがここに現れる。"""
        bus = tempfile.mkdtemp(prefix="kf-r9-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        cmd = [sys.executable, str(SCRIPT), "--bus", bus, "run", "x; y; z",
               "--workers", "2", "--planner", "stub", "--executor", "stub", "--poll", "0.2"]
        self.assertNotIn("--git", cmd, "R9: 単発実行は常駐体・分散同期に依存してはいけない")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        self.assertIsNotNone(final)
        self.assertTrue(all(r["status"] == "done" for r in final["results"].values()), final)

    def test_up_completes_all_tasks_once(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        results = final["results"]
        # fan-out-and-synthesize: 並列ノード + 統合ノード（合計 >= 4）がすべて done
        self.assertGreaterEqual(len(results), 4)
        self.assertIn("synth", results)
        for nid, r in results.items():
            self.assertEqual(r["status"], "done", f"{nid}: {r}")
            self.assertTrue(r["who"])  # 誰かが実行した
        self.assertIn("fan-out-and-synthesize", final["strategy"]["patterns"])
        # 集約パターンは既定で検証 gate が自動挿入される
        self.assertTrue(final["strategy"]["review"])
        self.assertIn("gate", results)

    def test_every_evaluation_round_is_recorded(self):
        """成果ゼロの評価ラウンドも run 履歴へ残ること（P4: 無言の欠番を作らない）。

        設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P4
        """
        bus = tempfile.mkdtemp(prefix="kf-eval-ev-")
        self.addCleanup(shutil.rmtree, bus, ignore_errors=True)
        p = self._run_up(bus, "x; y")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        view = kf.Bus(bus, run_id)
        evaluations = [e for e in view.recent_events(50) if e.get("kind") == "evaluate"]
        self.assertTrue(evaluations, "評価ラウンドの記録が無い")
        for event in evaluations:
            self.assertIn(event["decision"], ("done", "replan", "failed"))
            self.assertTrue(str(event.get("reason") or "").strip(), event)
            self.assertEqual(event["lenses"], ["duplication", "divergence", "verbosity"])

    def test_up_replan_recovers_failure(self):
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "good; FAIL bad")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        self.assertGreaterEqual(final["iterations"], 1)        # 再計画が回った
        self.assertEqual(final["results"]["t2r"]["status"], "done")  # retry 成功

    def test_up_map_reduce_with_review(self):
        # データ駆動 fan-out（split→map）＋統合前 gate（--review）の複合を end-to-end で
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "ファイルをそれぞれ処理して集約 3件", extra=["--review"])
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        final = self._final(bus)
        res = final["results"]
        # split → 3 map → gate(verify) → reduce がすべて done
        self.assertEqual(sum(1 for k in res if k.startswith("split1-m")), 3)
        self.assertIn("split1-gate", res)
        self.assertEqual(res["split1-reduce"]["status"], "done")
        self.assertEqual(res["split1-reduce"]["data"]["count"], 3)

    def test_result_command_presents_final_output(self):
        # 完了した run に対し result が最終成果（集約ノード synth）を返す
        bus = tempfile.mkdtemp(prefix="kf-e2e-")
        p = self._run_up(bus, "x; y; z")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        rp = subprocess.run(
            [sys.executable, str(SCRIPT), "--bus", bus, "--run-id", run_id,
             "result", "--json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(rp.returncode, 0, rp.stderr[-800:])
        out = json.loads(rp.stdout)
        self.assertTrue(out["done"])
        self.assertEqual(out["status"], "done")
        self.assertEqual([n["id"] for n in out["final_nodes"]], ["synth"])
        self.assertTrue(out["final_nodes"][0]["output"])
