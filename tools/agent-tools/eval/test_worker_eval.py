#!/usr/bin/env python3
"""多段セルの手順制御（run_steps）の単体テスト。LLM もエージェント CLI も呼ばない。

測りたいのは「ゲートが落ちたら**機械の不一致だけ**を足して同じ手順をやり直し、
通ったら次へ進む」こと。ここが崩れると T1seq / T1gate の比較が別物を測る。
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import worker_eval as w  # noqa: E402


class RunStepsTest(unittest.TestCase):
    def setUp(self):
        self.goals = []

    def _stub(self, results):
        """invoke の差し替え。呼ばれた goal を順に記録する。"""
        def fake(step, wt):
            self.goals.append(step["goal"])
            return 0, "out", "", 1.0
        self.addCleanup(mock.patch.object(w, "invoke", fake).stop)
        mock.patch.object(w, "invoke", fake).start()
        return results

    def test_single_goal_task_runs_once(self):
        self._stub(None)
        trace, out, _ = w.run_steps({"goal": "G"}, pathlib.Path("."))
        self.assertEqual(len(trace), 1)
        self.assertEqual(self.goals, ["G"])
        self.assertEqual(out, "out")

    def test_gate_pass_does_not_retry(self):
        self._stub(None)
        step = {"goal": "G", "gate": lambda wt: (True, "ok"), "max_retries": 2}
        trace, _, _ = w.run_steps({"steps": [step]}, pathlib.Path("."))
        self.assertEqual(len(trace), 1)
        self.assertTrue(trace[0]["gate"])

    def test_gate_fail_retries_with_machine_feedback_only(self):
        self._stub(None)
        step = {"goal": "G", "gate": lambda wt: (False, "BAD [(1, 'x', 'y')]"),
                "max_retries": 2}
        trace, _, _ = w.run_steps({"steps": [step]}, pathlib.Path("."))
        self.assertEqual(len(trace), 3)              # 初回 + 再試行 2
        self.assertEqual(self.goals[0], "G")
        # 再試行の課題文は「元の課題文 + 機械の不一致」だけ。積み増さない。
        self.assertTrue(self.goals[1].startswith("G"))
        self.assertIn("BAD [(1, 'x', 'y')]", self.goals[1])
        self.assertEqual(self.goals[1], self.goals[2])

    def test_steps_run_in_order_and_stop_retrying_once_gate_passes(self):
        seen = iter([False, True])
        self._stub(None)
        steps = [{"goal": "S1", "gate": lambda wt: (next(seen), "nope"), "max_retries": 3},
                 {"goal": "S2"}]
        trace, _, _ = w.run_steps({"steps": steps}, pathlib.Path("."))
        self.assertEqual(self.goals[0], "S1")
        self.assertTrue(self.goals[1].startswith("S1"))
        self.assertEqual(self.goals[2], "S2")
        self.assertEqual([s["step"] for s in trace], [1, 1, 2])


class ResampleTest(unittest.TestCase):
    """引き直し（best-of-N）。抽選は互いに独立で、採択は決定的ゲートだけが行う。

    「診断つき再投入」と「引き直し」は別物である。前者は前の成果を残したまま不一致を
    直させ、後者は前の成果を**捨てて**引き直す。ここが混ざると、既にある blind 腕と
    同じものを測ってしまう。
    """

    def setUp(self):
        self.goals: "list[str]" = []
        self.snapshots: "list[pathlib.Path]" = []
        self.restored: "list[dict]" = []

        def fake_invoke(step, wt):
            self.goals.append(step["goal"])
            return 0, "out", "", 1.0

        def fake_snapshot(wt):
            self.snapshots.append(wt)
            return {"files": {}, "dirs": []}

        self._patch("invoke", fake_invoke)
        self._patch("snapshot_worktree", fake_snapshot)
        self._patch("restore_worktree",
                    lambda wt, snapshot: self.restored.append(snapshot))

    def _patch(self, name: str, value) -> None:
        patcher = mock.patch.object(w, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _gated(self, gate, max_retries: int = 1) -> dict:
        return {"steps": [{"goal": "G", "gate": gate, "max_retries": max_retries}]}

    def test_default_arm_never_touches_the_worktree(self):
        """`--resample 1` は従来と同じ道を通る（既存の測定値と比較できる条件）。"""
        trace, _, _ = w.run_steps(self._gated(lambda wt: (False, "BAD")),
                                  pathlib.Path("."))
        self.assertEqual(len(trace), 2)             # 初回 + 診断つき再投入 1
        self.assertEqual([r["draw"] for r in trace], [1, 1])
        self.assertEqual(self.snapshots, [])        # 控えも戻しもしない
        self.assertEqual(self.restored, [])

    def test_redraw_discards_the_previous_attempt_and_its_diagnostic(self):
        self._patch("RESAMPLE", 2)
        trace, _, _ = w.run_steps(self._gated(lambda wt: (False, "BAD")),
                                  pathlib.Path("."))
        self.assertEqual([r["draw"] for r in trace], [1, 1, 2, 2])
        self.assertEqual([r["attempt"] for r in trace], [1, 2, 1, 2])
        self.assertEqual(self.goals[0], "G")
        self.assertIn("BAD", self.goals[1])         # 抽選の中では診断を渡す
        self.assertEqual(self.goals[2], "G")        # 引き直しは素の課題文へ戻る
        self.assertEqual(len(self.restored), 1)     # 戻すのは 2 本目の前だけ

    def test_a_passing_draw_stops_the_lottery(self):
        self._patch("RESAMPLE", 3)
        verdicts = iter([(False, "BAD"), (True, "ok")])
        trace, _, _ = w.run_steps(self._gated(lambda wt: next(verdicts), max_retries=0),
                                  pathlib.Path("."))
        self.assertEqual([r["draw"] for r in trace], [1, 2])
        self.assertTrue(trace[-1]["gate"])
        self.assertFalse(w.escalated(trace))

    def test_ungated_steps_are_never_redrawn(self):
        """採択する機械がいない手順を引き直すと、ただ複数回呼ぶだけになる。"""
        self._patch("RESAMPLE", 3)
        trace, _, _ = w.run_steps({"goal": "G"}, pathlib.Path("."))
        self.assertEqual(len(trace), 1)
        self.assertEqual(self.snapshots, [])
        self.assertEqual(self.restored, [])


class FailureFamilyTest(unittest.TestCase):
    """族（a / b）は台帳で escalate を分けて読むための軸。宣言漏れは黙って (a) にしない。"""

    def test_every_task_declares_a_family(self):
        for tid, task in w.TASKS.items():
            self.assertIn(task.get("family"), ("a", "b"), tid)

    def test_whole_work_omission_family_is_the_multi_deliverable_task(self):
        # (b) は元が複数成果物の課題（schema + 契約テスト）。分解 arm も同じ族として比較する。
        self.assertEqual({tid for tid, t in w.TASKS.items() if t["family"] == "b"},
                         {"T3", "T3gate", "T3splitgate"})


class T3SplitArmTest(unittest.TestCase):
    """丸ごと欠落族を、一成果物ずつの node + checker へ分けた比較 arm。"""

    def test_each_step_has_one_edit_artifact_and_its_own_gate(self):
        arm = w.TASKS["T3splitgate"]
        self.assertEqual(len(arm["steps"]), 2)
        self.assertEqual([step["files"] for step in arm["steps"]], [
            ("schemas/node-budget-summary.schema.json",),
            (w.T3_CONTRACT_TEST,),
        ])
        self.assertTrue(all(callable(step.get("gate")) for step in arm["steps"]))
        self.assertTrue(all(step.get("max_retries", 0) > 0 for step in arm["steps"]))

    def test_schema_gate_does_not_require_the_contract_test(self):
        with tempfile.TemporaryDirectory() as d:
            wt = pathlib.Path(d)
            schema = wt / "schemas" / "node-budget-summary.schema.json"
            schema.parent.mkdir()
            schema.write_text('{"type":"object","properties":{"remaining":{"type":"number"}}}',
                              encoding="utf-8")
            ok, note = w.check_t3_schema(wt)
        self.assertTrue(ok, note)

    def test_contract_gate_requires_the_named_test_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            wt = pathlib.Path(d)
            test_file = wt / w.T3_CONTRACT_TEST
            test_file.parent.mkdir(parents=True)
            with mock.patch.object(w, "_pytest", return_value=mock.Mock(returncode=0)):
                ok, _note = w.check_t3_split_contract(wt)
                self.assertFalse(ok)
                test_file.write_text("def test_contract():\n    assert True\n", encoding="utf-8")
                ok, note = w.check_t3_split_contract(wt)
        self.assertTrue(ok, note)


class SliceArmTest(unittest.TestCase):
    """T5（案 2 スライシングの A/B）の仕込みと抜粋。LLM は呼ばない。"""

    def _seeded(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="t5-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        w.seed_t5(tmp)
        return tmp

    def test_seed_fails_and_the_intended_fix_passes(self):
        wt = self._seeded()
        ok, note = w.check_t5(wt)
        self.assertFalse(ok, note)                       # 仕込みは落ちる（バグあり）
        fixed = w.REPORT_BUGGY.replace("return apply_tax(net, tax_rate)",
                                       "return apply_tax(net, int(round(tax_rate * 10000)))")
        (wt / "eval" / "report.py").write_text(fixed, encoding="utf-8")
        ok, note = w.check_t5(wt)
        self.assertTrue(ok, note)

    def test_bypassing_apply_tax_is_rejected_even_if_tests_pass(self):
        wt = self._seeded()
        bypass = w.REPORT_BUGGY.replace("return apply_tax(net, tax_rate)",
                                        "return int(net * (1 + tax_rate))")
        (wt / "eval" / "report.py").write_text(bypass, encoding="utf-8")
        ok, note = w.check_t5(wt)
        self.assertFalse(ok)
        self.assertIn("apply_tax", note)

    def test_editing_bigmod_or_tests_is_cheating(self):
        wt = self._seeded()
        (wt / "eval" / "bigmod.py").write_text(w.BIGMOD.replace("// 10000", "// 1"), encoding="utf-8")
        ok, note = w.check_t5(wt)
        self.assertFalse(ok)
        self.assertIn("bigmod.py", note)

    def test_slice_arm_passes_an_excerpt_that_keeps_the_unit_doc(self):
        wt = self._seeded()
        step, info = w.slice_reads(w.TASKS["T5slice"], wt)
        self.assertEqual(step["read"], ("eval/bigmod.slice.py",))
        meta = info["eval/bigmod.py"]
        self.assertTrue(meta["sliced"])
        self.assertLess(meta["kept_lines"], meta["total_lines"] // 5)   # 570 行 → 数十行
        excerpt = (wt / "eval" / "bigmod.slice.py").read_text(encoding="utf-8")
        self.assertIn("rate_bp", excerpt)
        self.assertIn("ベーシスポイント", excerpt)
        self.assertIn("def prorate", excerpt)
        self.assertNotIn("def util_01", excerpt)
        # 全文の腕・渡さない腕は抜粋を作らない
        self.assertEqual(w.slice_reads(w.TASKS["T5"], wt)[1], {})
        self.assertNotIn("read", w.TASKS["T5noread"])


class EscalateTest(unittest.TestCase):
    """上限到達の宣告。受入率とは別に数える（そのままクラウド昇格の頻度になる）。"""

    def test_exhausted_gate_is_an_escalation(self):
        self.assertTrue(w.escalated([{"step": 1, "draw": 1, "gate": False}]))

    def test_a_later_pass_clears_the_earlier_failure(self):
        self.assertFalse(w.escalated([{"step": 1, "draw": 1, "gate": False},
                                      {"step": 1, "draw": 2, "gate": True}]))

    def test_a_trace_without_gates_never_escalates(self):
        self.assertFalse(w.escalated([{"step": 1, "draw": 1}]))

    def test_one_failed_step_escalates_the_whole_run(self):
        self.assertTrue(w.escalated([{"step": 1, "gate": True},
                                     {"step": 2, "gate": False}]))


class WorktreeSnapshotTest(unittest.TestCase):
    """控えと戻しの git 配線を実ファイルで確かめる。

    ここが狂うと抽選が独立にならない（前の抽選の成果が残る）か、課題の仕込みごと
    消えて 2 本目以降が別の課題になる。どちらも台帳からは見分けが付かない。
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wt = pathlib.Path(tmp.name) / "wt"
        self.wt.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        (self.wt / "tracked.py").write_text("original\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.wt, check=True, capture_output=True)

    def test_restore_undoes_the_draw_but_keeps_the_seed(self):
        (self.wt / "eval").mkdir()
        (self.wt / "eval" / "seeded.py").write_text("seed\n", encoding="utf-8")
        snapshot = w.snapshot_worktree(self.wt)

        (self.wt / "tracked.py").write_text("edited\n", encoding="utf-8")
        (self.wt / "eval" / "seeded.py").write_text("clobbered\n", encoding="utf-8")
        (self.wt / "eval" / "draft.py").write_text("draft\n", encoding="utf-8")
        w.restore_worktree(self.wt, snapshot)

        self.assertEqual((self.wt / "tracked.py").read_text(encoding="utf-8"), "original\n")
        self.assertEqual((self.wt / "eval" / "seeded.py").read_text(encoding="utf-8"),
                         "seed\n")
        self.assertFalse((self.wt / "eval" / "draft.py").exists())

    def test_an_empty_seed_directory_survives(self):
        """seed_t1 が作る空の eval/ がこれ。git clean は控えていないと消してしまう。"""
        (self.wt / "eval").mkdir()
        snapshot = w.snapshot_worktree(self.wt)
        (self.wt / "eval" / "draft.py").write_text("draft\n", encoding="utf-8")
        w.restore_worktree(self.wt, snapshot)
        self.assertTrue((self.wt / "eval").is_dir())
        self.assertFalse((self.wt / "eval" / "draft.py").exists())

    def test_restore_refuses_to_run_on_the_repository_itself(self):
        with self.assertRaises(SystemExit):
            w.restore_worktree(w.REPO, {"files": {}, "dirs": []})


class AiderPolicyArgvTest(unittest.TestCase):
    def setUp(self):
        self.old = (w.MODEL, w.AGENT_POLICY, w.NUM_CTX, w.NUM_PREDICT, w.SAMPLING)
        w.MODEL = "gemma4:e4b"
        w.NUM_CTX = 0
        w.NUM_PREDICT = 0
        w.SAMPLING = {}
        self.addCleanup(self.restore)

    def restore(self):
        w.MODEL, w.AGENT_POLICY, w.NUM_CTX, w.NUM_PREDICT, w.SAMPLING = self.old

    def test_off_arm_removes_shipped_policy(self):
        w.AGENT_POLICY = "off"
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertNotIn("--agent-policy", argv)

    def test_unspecified_arm_inherits_shipped_policy(self):
        w.AGENT_POLICY = None
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertEqual(argv.count("--agent-policy"), 1)
        index = argv.index("--agent-policy")
        self.assertEqual(argv[index + 1], "gemma4-e4b-reliability-v1")

    def test_v1_arm_keeps_exactly_one_policy(self):
        w.AGENT_POLICY = "gemma4-e4b-reliability-v1"
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertEqual(argv.count("--agent-policy"), 1)
        index = argv.index("--agent-policy")
        self.assertEqual(argv[index + 1], w.AGENT_POLICY)

    def test_num_predict_uses_adapter_option(self):
        w.AGENT_POLICY = "gemma4-e4b-reliability-v1"
        w.NUM_PREDICT = 2048
        argv = w.aider_argv({"goal": "fix", "files": []})
        self.assertIn("--agent-num-predict", argv)
        self.assertNotIn("--model-settings-file", argv)

    def test_adapter_markers_are_parsed_as_typed_ledger_values(self):
        markers = w._agent_markers(
            "@agent-policy id=gemma4-e4b-reliability-v1 sha256=abc123\n"
            "@agent-usage tokens_in=12 tokens_out=4\n")
        self.assertEqual(markers, {
            "policy_id": "gemma4-e4b-reliability-v1", "policy_sha256": "abc123",
            "tokens_in": 12, "tokens_out": 4})


class TextTaskCheckerTest(unittest.TestCase):
    """T7digest / T8log の決定的チェッカー。LLM は呼ばない。"""

    def _wt(self, seed):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="t7t8-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        seed(tmp)
        return tmp

    def _good_digest(self):
        parts = ["# Tech Digest", "", "_2026-08-24 09:30 UTC_", ""]
        themes = {"AI・機械学習": w.T7_ARTICLES[:2],
                  "セキュリティ": w.T7_ARTICLES[2:4],
                  "クラウド・インフラ": w.T7_ARTICLES[4:]}
        for theme, arts in themes.items():
            parts += [f"## {theme}", ""]
            for a in arts:
                parts += [f"### [{a['title']}]({a['link']})", "",
                          "重要な発表についての日本語要約です。", ""]
        return "\n".join(parts) + "\n"

    def test_t7_passes_on_conforming_digest(self):
        wt = self._wt(w.seed_t7)
        (wt / "eval" / "digest.md").write_text(self._good_digest(), encoding="utf-8")
        ok, note = w.check_t7(wt)
        self.assertTrue(ok, note)

    def test_t7_fails_on_missing_file_link_theme_and_japanese(self):
        wt = self._wt(w.seed_t7)
        self.assertFalse(w.check_t7(wt)[0])                      # ファイルが無い
        good = self._good_digest()
        drop_link = good.replace(w.T7_ARTICLES[0]["link"], "https://example.com/other")
        (wt / "eval" / "digest.md").write_text(drop_link, encoding="utf-8")
        ok, note = w.check_t7(wt)
        self.assertFalse(ok)
        self.assertIn("リンク未掲載", note)
        one_theme = good.replace("## セキュリティ", "AI 続き").replace("## クラウド・インフラ", "AI 続き2")
        (wt / "eval" / "digest.md").write_text(one_theme, encoding="utf-8")
        ok, note = w.check_t7(wt)
        self.assertFalse(ok)
        self.assertIn("テーマ見出し", note)
        english_only = good.replace("重要な発表についての日本語要約です。", "An English-only summary.")
        (wt / "eval" / "digest.md").write_text(english_only, encoding="utf-8")
        ok, note = w.check_t7(wt)
        self.assertFalse(ok)
        self.assertIn("日本語要約", note)

    def _good_analysis(self):
        return ("# 障害解析\n\n## 根本原因\n\n"
                "payments-db のディスク枯渇（No space left on device, /var/lib/postgresql 100%）。"
                "09:14:10 に書き込みが失敗し、DB が read-only になった。\n\n"
                "## 波及\n\n"
                "1. api-gateway が payments-db:5432 へのタイムアウトを繰り返した\n"
                "2. checkout-service の注文 POST が 500 になった\n")

    def test_t8_passes_on_correct_root_cause(self):
        wt = self._wt(w.seed_t8)
        (wt / "eval" / "analysis.md").write_text(self._good_analysis(), encoding="utf-8")
        ok, note = w.check_t8(wt)
        self.assertTrue(ok, note)

    def test_t8_fails_when_stuck_at_symptom_or_decoy(self):
        wt = self._wt(w.seed_t8)
        self.assertFalse(w.check_t8(wt)[0])                      # ファイルが無い
        symptom = ("## 根本原因\n\napi-gateway のタイムアウトが多発。payments-db が応答しない。\n\n"
                   "## 波及\n\nしわ寄せ。\n")
        (wt / "eval" / "analysis.md").write_text(symptom, encoding="utf-8")
        ok, note = w.check_t8(wt)
        self.assertFalse(ok)
        self.assertIn("症状で止まっている", note)
        decoy = self._good_analysis().replace(
            "## 根本原因\n\npayments-db",
            "## 根本原因\n\nauth-service の deprecated 設定と payments-db")
        (wt / "eval" / "analysis.md").write_text(decoy, encoding="utf-8")
        ok, note = w.check_t8(wt)
        self.assertFalse(ok)
        self.assertIn("囮", note)
        no_section = self._good_analysis().replace("## 根本原因", "## 原因")
        (wt / "eval" / "analysis.md").write_text(no_section, encoding="utf-8")
        ok, note = w.check_t8(wt)
        self.assertFalse(ok)
        self.assertIn("根本原因", note)

    def test_t7gate_theme_gate_enforces_full_single_assignment(self):
        import json
        wt = self._wt(w.seed_t7)
        f = wt / "eval" / "themes.json"
        self.assertFalse(w.gate_t7_themes(wt)[0])                # ファイルが無い
        links = [a["link"] for a in w.T7_ARTICLES]
        f.write_text(json.dumps({"AI": links[:2], "セキュリティ": links[2:4],
                                 "クラウド": links[4:]}, ensure_ascii=False), encoding="utf-8")
        ok, note = w.gate_t7_themes(wt)
        self.assertTrue(ok, note)
        f.write_text(json.dumps({"AI": links[:5]}, ensure_ascii=False), encoding="utf-8")
        ok, note = w.gate_t7_themes(wt)                          # テーマ 1 つ + 未割当
        self.assertFalse(ok)
        f.write_text(json.dumps({"AI": links, "セキュリティ": links[:1]},
                                ensure_ascii=False), encoding="utf-8")
        ok, note = w.gate_t7_themes(wt)                          # 重複割当
        self.assertFalse(ok)
        self.assertIn("重複", note)

    def test_t8gate_evidence_gate_requires_root_cause_line(self):
        wt = self._wt(w.seed_t8)
        f = wt / "eval" / "evidence.md"
        self.assertFalse(w.gate_t8_evidence(wt)[0])              # ファイルが無い
        f.write_text("2026-08-24T09:14:15Z ERROR api-gateway: upstream timeout to "
                     "payments-db:5432 after 5000ms\nこれが最初のエラー。\n", encoding="utf-8")
        ok, note = w.gate_t8_evidence(wt)                        # 波及行を起点と誤認
        self.assertFalse(ok)
        self.assertIn("起点", note)
        f.write_text("2026-08-24T09:14:10Z ERROR payments-db: write failed: No space left "
                     "on device (/var/lib/postgresql, disk usage 100%)\n"
                     "この書き込み失敗が後続のタイムアウトの起点。\n", encoding="utf-8")
        ok, note = w.gate_t8_evidence(wt)
        self.assertTrue(ok, note)

    def test_t8_log_fixture_contains_cause_before_cascade(self):
        # 材料そのものの健全性: 原因行（disk）が波及行（timeout）より前にあり、囮も 1 行ある
        lines = w.T8_LOG.splitlines()
        cause = next(i for i, ln in enumerate(lines) if "No space left" in ln)
        first_timeout = next(i for i, ln in enumerate(lines) if "upstream timeout" in ln)
        self.assertLess(cause, first_timeout)
        self.assertTrue(any("deprecated" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()


class HarnessArmTests(unittest.TestCase):
    """実行方針の腕は名前だけにしない（agent-herd 設計 2026-08-27 §9 段 12・13）。

    名前が実際の設定を指していないと、条件の違う数字が同じ `harness` 値で台帳に並ぶ。
    起動時に断るところまでが軸の一部である。
    """

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).parent / "worker_eval.py"),
             "--tasks", "T2gate", *args],
            capture_output=True, text=True, timeout=60)

    def test_an_unknown_arm_is_refused(self):
        done = self._run("--harness", "nope")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("知らない --harness", done.stdout + done.stderr)

    def test_a_templates_arm_needs_an_existing_directory(self):
        done = self._run("--harness", "templates:/no/such/dir")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("宣言ディレクトリがありません", done.stdout + done.stderr)

    def test_templates_do_not_apply_to_aider(self):
        """aider は自前のシステムプロンプトで走る——効かない条件に腕名を付けない。"""
        with tempfile.TemporaryDirectory() as tmp:
            done = self._run("--cli", "aider", "--harness", f"templates:{tmp}")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("agent-ollama 経路のみ", done.stdout + done.stderr)
