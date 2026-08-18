"""agent-flow の単体テスト — 公開後の CI 結果の取り込み（P6 の書き手）。

公開レコード（結果ノードの publication）へ CI の状態を書き戻す経路。CI クライアントは
持たず、利用者が宣言したコマンドの JSON 出力を正典にする。

設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P6

    python -m unittest tests.test_ci_status
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・kf ロード・共通ヘルパ）


PUBLICATION = {
    "state": "published", "url": "git@example.invalid:repo.git",
    "branch": "af/run-1", "commit": "a" * 40,
}


class CiStateTests(unittest.TestCase):
    def test_known_state_words_map_to_four_values(self):
        self.assertEqual(kf.ci_state("success"), "passed")
        self.assertEqual(kf.ci_state("SUCCESS"), "passed")
        self.assertEqual(kf.ci_state("failure"), "failed")
        self.assertEqual(kf.ci_state("cancelled"), "failed")
        self.assertEqual(kf.ci_state("in_progress"), "running")
        self.assertEqual(kf.ci_state("queued"), "running")

    def test_unknown_word_never_becomes_passed(self):
        # 知らない語を緑へ倒すと、赤い CI が「完了」として下流へ流れる
        for word in ("", None, "weird", "maybe", "skipped"):
            self.assertEqual(kf.ci_state(word), "unknown", word)

    def test_report_normalizes_checks_and_drops_broken_entries(self):
        report = kf.normalize_ci_report({
            "state": "failure", "url": "https://ci.example/1",
            "checks": [{"name": "dashboard", "conclusion": "failure", "url": "u"},
                       {"name": "", "state": "success"}, "壊れた要素",
                       {"name": "docs", "state": "success"}],
            "note": "1 件失敗",
        }, checked_at="2026-08-16T00:00:00Z")
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["checked_at"], "2026-08-16T00:00:00Z")
        self.assertEqual([c["name"] for c in report["checks"]], ["dashboard", "docs"])
        self.assertEqual(report["checks"][0]["state"], "failed")
        self.assertEqual(report["detail"], "1 件失敗")

    def test_non_dict_report_is_not_recorded(self):
        self.assertIsNone(kf.normalize_ci_report(None))
        self.assertIsNone(kf.normalize_ci_report(["passed"]))


class CiCollectTests(unittest.TestCase):
    def test_single_shot_without_wait(self):
        calls = []

        def runner():
            calls.append(1)
            return {"state": "running", "checked_at": "t"}

        report = kf.collect_ci_status(PUBLICATION, "true", runner=runner,
                                      sleeper=lambda _s: None)
        self.assertEqual(report["state"], "running")
        self.assertEqual(len(calls), 1, "待ち時間 0 では 1 回だけ問い合わせる")

    def test_waits_until_terminal_state(self):
        states = iter(["running", "running", "failed"])
        slept = []
        report = kf.collect_ci_status(
            PUBLICATION, "true", wait_seconds=120, poll_seconds=10,
            runner=lambda: {"state": next(states)}, sleeper=slept.append)
        self.assertEqual(report["state"], "failed")
        self.assertEqual(slept, [10, 10])

    def test_wait_budget_is_bounded(self):
        slept = []
        report = kf.collect_ci_status(
            PUBLICATION, "true", wait_seconds=25, poll_seconds=10,
            runner=lambda: {"state": "running"}, sleeper=slept.append)
        # 予算を超えて待たない。終端しなければ「まだ回っている」をそのまま記録する
        self.assertEqual(report["state"], "running")
        self.assertEqual(slept, [10, 10, 5])

    def test_no_command_or_no_commit_records_nothing(self):
        self.assertIsNone(kf.collect_ci_status(PUBLICATION, ""))
        self.assertIsNone(kf.collect_ci_status({"state": "published"}, "true",
                                               runner=lambda: {"state": "passed"}))

    def test_command_output_is_the_source_of_truth(self):
        report = kf.collect_ci_status(
            PUBLICATION,
            """printf '{"state":"success","url":"https://ci.example/%s"}' "$AGENT_CI_BRANCH" """)
        self.assertEqual(report["state"], "passed")
        self.assertEqual(report["url"], "https://ci.example/af/run-1")

    def test_broken_command_is_unknown_not_green(self):
        self.assertEqual(kf.collect_ci_status(PUBLICATION, "exit 3")["state"], "unknown")
        self.assertEqual(kf.collect_ci_status(PUBLICATION, "echo これはJSONではない")["state"], "unknown")
        self.assertIn("JSON", kf.collect_ci_status(PUBLICATION, "echo not-json")["detail"])


class CiAttachTests(unittest.TestCase):
    def _bus(self, results):
        root = tempfile.mkdtemp(prefix="kf-ci-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        bus = kf.Bus(root, "run-ci")
        bus.ensure_dirs()
        bus.write_graph({"nodes": {node_id: {"id": node_id, "goal": "g", "deps": [], "kind": "work"}
                                   for node_id in results}, "iteration": 0})
        for node_id, result in results.items():
            kf.write_json_atomic(bus.result_path(node_id), result)
        return bus

    def _args(self, **kw):
        base = dict(ci_status_command="true", ci_wait_seconds=0, ci_poll_seconds=1)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_written_back_into_the_publication_record(self):
        bus = self._bus({
            "build": {"status": "done", "data": {"publication": dict(PUBLICATION)}},
            "docs": {"status": "done", "data": {"delivery": {"publication": dict(PUBLICATION)}}},
            "plain": {"status": "done", "output": "公開なし"},
        })
        overall = kf.attach_ci_results(
            bus, self._args(),
            collector=lambda *a, **k: {"state": "failed", "url": "https://ci.example/9"})

        self.assertEqual(overall["state"], "failed")
        self.assertEqual(bus.read_result("build")["data"]["publication"]["ci"]["state"], "failed")
        self.assertEqual(
            bus.read_result("docs")["data"]["delivery"]["publication"]["ci"]["state"], "failed")
        self.assertNotIn("ci", bus.read_result("plain").get("data") or {})

    def test_same_commit_is_asked_once(self):
        bus = self._bus({
            "a": {"status": "done", "data": {"publication": dict(PUBLICATION)}},
            "b": {"status": "done", "data": {"publication": dict(PUBLICATION)}},
        })
        calls = []
        kf.attach_ci_results(bus, self._args(),
                            collector=lambda *a, **k: calls.append(1) or {"state": "passed"})
        self.assertEqual(len(calls), 1)

    def test_disabled_or_unpublished_run_records_nothing(self):
        bus = self._bus({"a": {"status": "done", "data": {"publication": dict(PUBLICATION)}}})
        self.assertIsNone(kf.attach_ci_results(bus, self._args(ci_status_command="")))
        failed = self._bus({"a": {"status": "failed", "data": {"publication": {
            **PUBLICATION, "state": "failed"}}}})
        self.assertIsNone(kf.attach_ci_results(failed, self._args(),
                                               collector=lambda *a, **k: {"state": "passed"}))

    def test_worst_state_wins_across_records(self):
        self.assertEqual(kf._worst_ci_state({"passed", "failed"}), "failed")
        self.assertEqual(kf._worst_ci_state({"passed", "running"}), "running")
        self.assertEqual(kf._worst_ci_state({"passed", "unknown"}), "unknown")
        self.assertEqual(kf._worst_ci_state({"passed"}), "passed")
        self.assertEqual(kf._worst_ci_state(set()), "unknown")

    def test_config_defaults_reach_args(self):
        args = types.SimpleNamespace(config=None, ci_status_command=None,
                                     ci_wait_seconds=None, ci_poll_seconds=None)
        kf.resolve_config(args)
        self.assertEqual(args.ci_status_command, "")
        self.assertEqual(args.ci_wait_seconds, 0.0)
        self.assertEqual(args.ci_poll_seconds, 30.0)
