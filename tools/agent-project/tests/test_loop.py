"""agent-project の単体テスト — loop（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _submit_feedback  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class TestRunlogAndThrottle(unittest.TestCase):
    """構造化 run-log（JSONL）と自動スロットル（ソフト予算→打ち切り・watch は report 降格）。"""

    def _cfg(self, d, **kw):
        return cfg_for(Path(d), dry_run=False, learn=False, auto_adjudicate=False,
                       max_cycles=50, do_archive=True, **kw)

    def _cost_act(self, usd=0.03):
        return lambda t, c, loc: (True, f"done\n@cost tokens=100 usd={usd}")

    def test_runlog_written_per_pass(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(self._cfg(d), act=lambda t, c, loc: (True, "ok"))
            lines = (d / "run-log.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            rec = __import__("json").loads(lines[0])
            self.assertEqual(rec["done"], 2)
            self.assertEqual(rec["reason"], res["reason"])
            for k in ("ts", "reason", "cycles", "escalations", "tokens", "cost", "duration_s"):
                self.assertIn(k, rec)

    def test_named_node_also_writes_immutable_run_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run-log.jsonl"
            km.append_runlog(path, {"run_id": "run-1", "node": "pc-a", "reason": "drained"})
            record = Path(d) / "run-log" / "pc-a" / "run-1.json"
            self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["reason"], "drained")

    def test_throttle_stops_before_hard_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, max_cost=0.10, throttle=0.8),
                              act=self._cost_act(0.03))
            self.assertEqual(res["reason"], "throttle")        # 0.8*0.10=0.08 で打ち切り
            self.assertLess(res["cost"], 0.10)                 # ハード上限の手前
            self.assertEqual(km.exit_code_for(res), 2)

    def test_throttle_off_uses_hard_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            res = km.run_loop(self._cfg(d, max_cost=0.10, throttle=0.0),
                              act=self._cost_act(0.03))
            self.assertEqual(res["reason"], "cost")            # throttle off → ハード上限で停止

    def test_watch_degrades_to_report_on_throttle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(6):
                mkb(d, f"T{i}", verify="true")
            cfg = self._cfg(d, max_cost=0.10, throttle=0.8)
            km.run_watch(cfg, act=self._cost_act(0.03), sleeper=lambda s: None, max_passes=2)
            self.assertEqual(cfg.level, "report")              # throttle 後は report 降格

    def test_cmd_runlog(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(km.cmd_runlog(self._cfg(d)), 0)   # 空でも落ちない
            mkb(d, "T1", verify="true")
            km.run_loop(self._cfg(d), act=lambda t, c, loc: (True, "ok"))
            self.assertEqual(km.cmd_runlog(self._cfg(d), as_json=True, tail=5), 0)


class TestRunLoop(unittest.TestCase):
    def test_drains_and_archives_done(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true"); mkb(d, "T2", verify="true")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertEqual(res["counts"]["done"], 2)
            self.assertEqual(res["archived"], 2)
            self.assertEqual(km.exit_code_for(res), 0)
            # backlog からは消え、archive/ へ移動（退避ファイルに archived 行）
            self.assertEqual(list((d / "backlog").glob("*.md")), [])
            self.assertTrue((d / "archive" / "T1.md").exists())
            self.assertIn("archived:", (d / "archive" / "T1.md").read_text())

    def test_no_archive_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            res = km.run_loop(cfg_for(d, do_archive=False))
            self.assertEqual(res["archived"], 0)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])
            self.assertFalse((d / "archive").exists())

    def test_ng_restacks_then_blocks_with_needs_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=2))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertEqual(km.exit_code_for(res), 1)
            self.assertTrue((d / "backlog" / "T1.md").exists())
            self.assertTrue((d / "needs" / "T1.md").exists())

    def test_needs_file_includes_evidence(self):
        # blocked の needs に「判断材料（所在・差分・検証）」が載り、人がレビューせず判断できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="test -f never_exists")     # 必ず FAIL
            km.run_loop(cfg_for(d, max_retries=0))
            body = (d / "needs" / "T1.md").read_text(encoding="utf-8")
            self.assertIn("## 判断材料", body)
            self.assertIn("- 成果物:", body)
            self.assertIn("- 所在:", body)
            self.assertIn("- 検証:", body)
            self.assertIn("FAIL", body)

    def test_delivery_evidence_reports_location_and_diff(self):
        # delivery_evidence が所在（ブランチ）・差分・検証を含む
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            subprocess.run(["git", "-C", str(d), "init", "-q", "-b", "feat"], check=True,
                           capture_output=True)
            (d / "a.txt").write_text("x")
            for c in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
                subprocess.run(["git", "-C", str(d)] + c, check=True, capture_output=True)
            base = km.git_change_baseline(d)
            (d / "b.txt").write_text("y")                    # baseline 以降の変更
            ev = km.delivery_evidence(cfg_for(d, workdir=d),
                                      "https://gitlab.com/g/r/merge_requests/7",
                                      base, location="remote", verify="true", vmsg="ok", ok=True)
            self.assertIn("merge_requests/7", ev)            # 成果物 ref（MR URL）
            self.assertIn("ブランチ feat", ev)               # 所在ブランチ
            self.assertIn("b.txt", ev)                       # 差分
            self.assertIn("→ PASS", ev)                      # 検証

    def test_delivery_evidence_verify_undefined_is_not_fail(self):
        # verify 未定義（空文字）は FAIL でなく「確認待ち」として書く（失敗と誤読させない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ev = km.delivery_evidence(cfg_for(d, workdir=d), "", None, location="local",
                                      verify="", vmsg="verify 未定義（自己申告では done にできない）",
                                      ok=False)
            self.assertIn("- 検証: 未定義", ev)
            self.assertNotIn("FAIL", ev)

    def test_delivery_evidence_verify_not_run_is_not_fail(self):
        """act が失敗して検証まで到達しなかった記録を FAIL と書かない。

        bool では「実行して落ちた」と「そこまで到達していない」が同じ False に潰れる。
        潰すと、着手前に止まった run の判断材料に「検証 → FAIL」が残り、画面は
        「検証コマンドが失敗しました」と表示する。verify は一度も走っていないので、
        人は存在しないテスト失敗を調べに行き、本当の原因には辿り着けない。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ev = km.delivery_evidence(
                cfg_for(d, workdir=d), "", None, location="local",
                verify="pytest -q", vmsg="", ok=False, verdict=km.VERIFY_NOT_RUN)
            self.assertIn("→ 未実行", ev)
            self.assertNotIn("FAIL", ev)
            # 実行された失敗は従来どおり FAIL（未実行と取り違えない）
            ran = km.delivery_evidence(
                cfg_for(d, workdir=d), "", None, location="local",
                verify="pytest -q", vmsg="1 failed", ok=False)
            self.assertIn("→ FAIL", ran)
            self.assertNotIn("未実行", ran)

    def test_diagnose_verify_failure_reads_raw_output(self):
        """検証の解釈は生の verify 出力から行う（判断材料の散文を読み直さない）。

        以前この解釈は agent-dashboard 側にあり、agent-project が書いた散文を正規表現で
        読み直していた。書き手の文言が変わると読み手だけが静かに壊れる。"""
        d = km.diagnose_verify_failure("pytest -q", "exit=1 3 failed, 20 passed")
        self.assertEqual(d["summary"], "テストが 3 件失敗しました。")
        self.assertEqual(d["category"], "テスト失敗")
        self.assertEqual(d["exit_code"], "1")
        d = km.diagnose_verify_failure("x", "exit=127 foo: command not found")
        self.assertIn("「foo」", d["summary"])
        self.assertEqual(d["owner"], "検査設定・実行環境")
        d = km.diagnose_verify_failure("a && b", "exit=1 失敗した工程: `grep -q x README.md`")
        self.assertIn("grep -q x README.md", d["summary"])
        self.assertEqual(d["command"], "grep -q x README.md")
        # テストは通っているが後段が落ちた（「テストの失敗ではない」と言えることが要点）
        d = km.diagnose_verify_failure("a && b", "exit=2 29 passed")
        self.assertIn("29 件成功", d["summary"])
        # 解釈できなければ空。空は「分からない」であって「失敗していない」ではない
        self.assertEqual(km.diagnose_verify_failure("x", "")["summary"], "")
        self.assertEqual(km.diagnose_verify_failure("x", "なにかよく分からない出力")["summary"], "")

    def test_needs_file_carries_structured_failure(self):
        """needs の frontmatter に失敗の構造化フィールドが載る（表示層は読むだけ）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", verify="false")
            t = km.load_tasks(cfg.backlog)[0]
            km.write_needs_file(cfg, t, "検証に失敗", failure={
                "cls": "control", "chain": ["control", "quota"],
                "phase": km.PHASE_ACT, "verdict": km.VERIFY_NOT_RUN,
                "summary": "テストが 3 件失敗しました。", "exit_code": "1",
            })
            fm = km.needs_path(cfg, "T1").read_text(encoding="utf-8").split("---")[1]
            self.assertIn("failure-class: control", fm)
            self.assertIn("failure-chain: control,quota", fm)
            self.assertIn(f"failure-phase: {km.PHASE_ACT}", fm)
            self.assertIn(f"verify-verdict: {km.VERIFY_NOT_RUN}", fm)
            self.assertIn("failure-summary: テストが 3 件失敗しました。", fm)
            self.assertIn("failure-exit: 1", fm)

    def test_needs_file_without_failure_keeps_old_shape(self):
        """失敗情報が無い票は従来どおりの frontmatter（旧記録と同じ見た目を保つ）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1")
            t = km.load_tasks(cfg.backlog)[0]
            km.write_needs_file(cfg, t, "ふつうのブロック")
            fm = km.needs_path(cfg, "T1").read_text(encoding="utf-8").split("---")[1]
            self.assertNotIn("failure-", fm)
            self.assertNotIn("verify-verdict", fm)

    def test_verify_verdict_normalizes_ok_and_ran(self):
        self.assertEqual(km.verify_verdict(True), km.VERIFY_PASSED)
        self.assertEqual(km.verify_verdict(False), km.VERIFY_FAILED)
        self.assertEqual(km.verify_verdict(None), km.VERIFY_UNKNOWN)
        self.assertEqual(km.verify_verdict(False, ran=False), km.VERIFY_NOT_RUN)
        self.assertEqual(km.verify_verdict(True, ran=False), km.VERIFY_NOT_RUN)

    def test_budget_stop(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="false")
            res = km.run_loop(cfg_for(d, max_retries=999, max_cycles=4))
            self.assertEqual(res["reason"], km.REASON_BUDGET)
            self.assertEqual(res["cycles"], 4)
            self.assertEqual(km.exit_code_for(res), 2)

    def test_no_verify_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="")
            res = km.run_loop(cfg_for(d))
            self.assertEqual(res["counts"]["blocked"], 1)
            self.assertTrue((d / "needs" / "T1.md").exists())

    def test_act_injection_local(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            marker = d / "acted"
            mkb(d, "T1", verify=f"test -f {marker}")
            calls = []

            def fake_act(task, cfg, location="local"):
                calls.append((task.id, location))
                marker.write_text("x")
                return True, "ok"

            res = km.run_loop(cfg_for(d, dry_run=False), act=fake_act)
            self.assertEqual(calls, [("T1", "local")])
            self.assertEqual(res["counts"]["done"], 1)


class TestActSubmitTerminal(unittest.TestCase):
    """daemon/remote submit 待ちが agent-flow run の終端 status を正しく解釈する。
    failed を success と取り違えず、orchestrator 異常終了（daemon が failed に確定）でも
    execute フェーズが永久待機せず即座に失敗として返ることを検証する。"""

    def _fake_run(self, result_payload, advance=None):
        """submit は run-id を返し、result --json は result_payload を返す擬似 subprocess.run。"""
        def fake(cmd, *a, **kw):
            if "submit" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="run-XYZ\n", stderr="")
            if "result" in cmd:
                if advance is not None:
                    advance()
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(result_payload), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return fake

    def _task(self):
        return km.Task(id="T1", title="x", verify="true")

    def test_failed_run_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            with mock.patch.object(km.subprocess, "run",
                                   self._fake_run({"done": True, "status": "failed"})), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertFalse(ok)              # failed を success と取り違えない
            self.assertIn("failed", msg)

    def test_canceled_run_reported_as_failure(self):
        # dashboard からの手動キャンセルを done（成功）と取り違えない
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            with mock.patch.object(km.subprocess, "run",
                                   self._fake_run({"done": True, "status": "cancelled"})), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertFalse(ok)
            self.assertIn("cancelled", msg)

    def test_done_run_reported_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            with mock.patch.object(km.subprocess, "run",
                                   self._fake_run({"done": True, "status": "done"})), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertTrue(ok)
            self.assertIn("done", msg)

    def test_submit_req_id_deterministic_and_passed_to_submit(self):
        # リブート跨ぎの再接続の前提: 同一試行は同じ req_id（決定的）、リトライ・別プロジェクトは別 id
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            t = self._task()
            rid = km._submit_req_id(t, cfg)
            self.assertEqual(rid, km._submit_req_id(t, cfg))                  # 決定的
            self.assertNotEqual(rid, km._submit_req_id(
                km.Task(id="T1", title="x", verify="true", retries=1), cfg))  # リトライは新 run
            cfg2 = cfg_for(Path(d) / "other", dry_run=False)
            self.assertNotEqual(rid, km._submit_req_id(t, cfg2))              # 別 backlog と衝突しない
            self.assertNotIn("/", rid)                                        # run ディレクトリ名に安全

            seen = []

            def fake(cmd, *a, **kw):
                seen.append(list(cmd))
                if "submit" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=f"{rid}\n", stderr="")
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"done": True, "status": "done"}), stderr="")

            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "sleep", lambda *_: None):
                ok, _ = km._act_submit(t, cfg, use_git=False)
            self.assertTrue(ok)
            sub_cmd = next(c for c in seen if "submit" in c)
            self.assertIn("--run-id", sub_cmd)                                # 再接続の入口
            self.assertEqual(sub_cmd[sub_cmd.index("--run-id") + 1], rid)

    def test_nonterminal_run_times_out_without_hanging(self):
        # done=False のまま（orchestrator 失踪を daemon が終端化できていない最悪ケース）でも、
        # act_timeout を境に必ず返る（永久待機しない）ことを擬似クロックで確認する。
        clock = [1000.0]
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=10.0)
            fake = self._fake_run({"done": False, "status": "running"})
            reaped = []
            detached = []
            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "time", lambda: clock[0]), \
                 mock.patch.object(km.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                 mock.patch.object(km, "reap_orphan_flow",
                                   side_effect=lambda *a, **k: reaped.append(True) or 0), \
                 mock.patch.object(km, "detach_flow_run",
                                   side_effect=lambda cfg, task, reason="", **kw: (
                                       detached.append(reason) or "run-XYZ")):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertFalse(ok)
            self.assertIn("タイムアウト", msg)
            self.assertEqual(reaped, [], "submit タイムアウトは daemon 全滅 reap せず cancel する")
            self.assertEqual(len(detached), 1, "対象 run だけ detach（cancel）する")
            self.assertIn("タイムアウト", detached[0])


class TestActRunMidRevise(unittest.TestCase):
    """同期 _act_run も submit と同様、実行中 revise で切り離す。"""

    def test_mid_revise_detaches_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, dry_run=False, act_timeout=30.0)
            (cfg.backlog).mkdir(parents=True, exist_ok=True)
            t = km.Task(id="T1", title="x", verify="true", status="doing")
            (cfg.backlog / "T1.md").write_text(km.serialize_task(t), encoding="utf-8")

            class FakeProc:
                def __init__(self):
                    self._n = 0
                    self.stdout = io.StringIO("")
                    self.returncode = None

                def poll(self):
                    self._n += 1
                    return None  # ずっと実行中

                def terminate(self):
                    self.returncode = -15

                def kill(self):
                    self.returncode = -9

                def wait(self, timeout=None):
                    return self.returncode

            # revise: 初回 None、2 回目以降 revise
            checks = [False]

            def abort_reason(_cfg, _task, _rid):
                if not checks[0]:
                    checks[0] = True
                    return None
                return "revise"

            with mock.patch.object(km.subprocess, "Popen", return_value=FakeProc()), \
                 mock.patch.object(km, "_wait_abort_reason", side_effect=abort_reason), \
                 mock.patch.object(km.time, "sleep", lambda *_: None), \
                 mock.patch.object(km, "reap_orphan_flow", return_value=0):
                ok, msg = km._act_run(t, cfg, use_git=False)
            self.assertFalse(ok)
            self.assertIn("revise", msg)
            self.assertIsNone(t.get("flow_run"), "detach で flow_run を外す")

    def test_detach_keeps_cancel_marker_when_no_meta(self):
        """submit 前 detach: マーカーを残し daemon の run 化前 cancel へ渡す。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", verify="true", status="doing")
            t.set("flow_run", "req-not-yet")
            rid = km.detach_flow_run(cfg, t, "事前キャンセル")
            self.assertEqual(rid, "req-not-yet")
            self.assertTrue((cfg.bus / "inbox" / "cancels" / "req-not-yet.json").is_file())

    def test_wait_abort_reason_sees_approve_detach(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", verify="true", status="doing")
            t.set("flow_run", "run-X")
            (cfg.backlog / "T1.md").write_text(km.serialize_task(t), encoding="utf-8")
            self.assertIsNone(km._wait_abort_reason(cfg, t, "run-X"))
            # approve 相当: flow_run を外して ready
            live = km.parse_task((cfg.backlog / "T1.md").read_text(encoding="utf-8"), "T1")
            live.drop("flow_run")
            live.status = "ready"
            (cfg.backlog / "T1.md").write_text(km.serialize_task(live), encoding="utf-8")
            self.assertEqual(km._wait_abort_reason(cfg, t, "run-X"), "detach")

    def test_inherit_skips_canceled_last_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False)
            old = "req-ab-T1-r0"
            (cfg.bus / "runs" / old).mkdir(parents=True)
            (cfg.bus / "runs" / old / "meta.json").write_text(
                json.dumps({"status": "cancelled", "request": "x"}), encoding="utf-8")
            t = km.Task(id="T1", title="x", verify="true", retries=1)
            t.extra.append(("last_run", old))
            self.assertIsNone(km._inherit_from_run(t, "req-ab-T1-r1", cfg))
            prev = km._inherit_from_run(t, "req-ab-T1-r1", cfg)
            if prev is None and not str(t.get("last_run") or "").strip():
                prev = km._prev_req_id(t, cfg)
            self.assertIsNone(prev)

    def test_timeout_detach_marks_failed_and_inherits(self):
        # タイムアウトは cancelled ではなく failed。次 run は last_run を inherit できる。
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False)
            km.ensure_dirs(cfg)
            old = "req-to-T1-r0"
            (cfg.bus / "runs" / old).mkdir(parents=True)
            (cfg.bus / "runs" / old / "meta.json").write_text(
                json.dumps({"status": "running", "request": "x"}), encoding="utf-8")
            t = km.Task(id="T1", title="x", verify="true", status="doing", retries=0)
            t.set("flow_run", old)
            (cfg.backlog / "T1.md").write_text(km.serialize_task(t), encoding="utf-8")
            rid = km.detach_flow_run(cfg, t, "agent-flow run タイムアウト（1800.0s）",
                                     failed=True)
            self.assertEqual(rid, old)
            meta = json.loads((cfg.bus / "runs" / old / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "failed")
            self.assertIn("タイムアウト", meta.get("failure_reason", ""))
            self.assertNotIn("cancel_reason", meta)
            t.retries = 1
            t.extra.append(("last_run", old))
            self.assertEqual(km._inherit_from_run(t, "req-to-T1-r1", cfg), old)

    def test_human_detach_stays_canceled_no_inherit(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False)
            km.ensure_dirs(cfg)
            old = "req-hum-T1-r0"
            (cfg.bus / "runs" / old).mkdir(parents=True)
            (cfg.bus / "runs" / old / "meta.json").write_text(
                json.dumps({"status": "running", "request": "x"}), encoding="utf-8")
            t = km.Task(id="T1", title="x", verify="true", status="doing")
            t.set("flow_run", old)
            km.detach_flow_run(cfg, t, "revise により委譲から切り離し")
            meta = json.loads((cfg.bus / "runs" / old / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "cancelled")
            t.retries = 1
            t.extra.append(("last_run", old))
            self.assertIsNone(km._inherit_from_run(t, "req-hum-T1-r1", cfg))


class TestActTimeoutZeroAndInherit(unittest.TestCase):
    """act_timeout=0（無制限待ち）と、リトライ時の先行 run 引き継ぎ（--inherit-from）の配線。
    gitlab 等の長時間委譲で待ち切れず retry を空増やしする事故を防ぐための変更。"""

    def _task(self, retries=0):
        return km.Task(id="T1", title="x", verify="true", retries=retries)

    def test_claim_ttl_infinite_when_act_timeout_zero(self):
        with tempfile.TemporaryDirectory() as d:
            cfg0 = cfg_for(Path(d), dry_run=False, act_timeout=0.0)
            self.assertEqual(km._claim_ttl(cfg0), float("inf"))   # 委譲中に claim を奪われない
            cfg30 = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            self.assertTrue(km._claim_ttl(cfg30) < float("inf"))

    def test_act_timeout_zero_waits_until_done(self):
        # act_timeout=0 は無制限。擬似クロックが大きく進んでもタイムアウトせず、done で success。
        clock = [1000.0]
        state = {"polls": 0}

        def fake(cmd, *a, **kw):
            if "submit" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="run-XYZ\n", stderr="")
            if "result" in cmd:
                state["polls"] += 1
                done = state["polls"] >= 5
                payload = {"done": done, "status": "done" if done else "running"}
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=0.0)
            with mock.patch.object(km.subprocess, "run", fake), \
                 mock.patch.object(km.time, "time", lambda: clock[0]), \
                 mock.patch.object(km.time, "sleep",
                                   lambda s: clock.__setitem__(0, clock[0] + 100000)):
                ok, msg = km._act_submit(self._task(), cfg, use_git=False)
            self.assertTrue(ok)                          # 巨大なクロック前進でもタイムアウトしない
            self.assertIn("done", msg)
            self.assertGreaterEqual(state["polls"], 5)

    def test_inherit_from_passed_on_retry_only(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), dry_run=False, act_timeout=30.0)
            self.assertIsNone(km._prev_req_id(self._task(0), cfg))          # 初回は先行 run なし
            self.assertEqual(km._prev_req_id(self._task(2), cfg),
                             km._req_id_for(self._task(2), cfg, 1))         # retries-1 世代

            def capture(retries):
                seen = []

                def fake(cmd, *a, **kw):
                    seen.append(list(cmd))
                    if "submit" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, stdout="rid\n", stderr="")
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=json.dumps({"done": True, "status": "done"}), stderr="")

                with mock.patch.object(km.subprocess, "run", fake), \
                     mock.patch.object(km.time, "sleep", lambda *_: None):
                    km._act_submit(self._task(retries), cfg, use_git=False)
                return next(c for c in seen if "submit" in c)

            self.assertNotIn("--inherit-from", capture(0))                  # 初回は引き継ぎなし
            retry = capture(3)
            self.assertIn("--inherit-from", retry)                         # リトライは引き継ぐ
            self.assertEqual(retry[retry.index("--inherit-from") + 1],
                             km._req_id_for(self._task(3), cfg, 2))


class TestPace(unittest.TestCase):
    def test_decide_pace(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertAlmostEqual(km.decide_pace(cfg_for(d, pace=5.0), 2.0), 3.0)
            self.assertEqual(km.decide_pace(cfg_for(d, pace=5.0), 9.0), 0.0)
            self.assertAlmostEqual(
                km.decide_pace(cfg_for(d, max_seconds=20.0, max_cycles=10), 0.5), 1.5)

    def test_run_calls_sleeper(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1"); mkb(d, "T2")
            slept = []
            km.run_loop(cfg_for(d, pace=3.0), sleeper=lambda s: slept.append(s))
            self.assertTrue(slept and all(s > 0 for s in slept))


class TestWatch(unittest.TestCase):
    def test_watch_picks_up_new_task(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            cfg = cfg_for(d)

            def slp(_):
                mkb(d, "T2", verify="true")  # idle 中に人が新タスク投入した想定

            last = km.run_watch(cfg, sleeper=slp, max_passes=2)
            self.assertEqual(last["reason"], km.REASON_DRAINED)
            self.assertEqual(list((d / "backlog").glob("*.md")), [])


class TestWatchGracefulExit(unittest.TestCase):
    """watch の graceful 停止。start / stop / restart と インスタンスレジストリは
    廃止した（実装計画 W1-9 — 常駐は `agent-project serve` の 1 本、二重監視の防止は
    OS の排他ロック）。停止そのものの契約はここに残す。"""

    def test_watch_sigterm_graceful_exit(self):
        # SIGTERM 化された KeyboardInterrupt は graceful 停止: traceback を出さず 0 で終える
        # （README の「stop は graceful…終了」を担保）。
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), watch=True)
            saved = signal.getsignal(signal.SIGTERM)
            try:
                with mock.patch.object(km, "run_watch", side_effect=KeyboardInterrupt):
                    rc = km.cmd_run(cfg)        # 例外は伝播せず捕捉される
            finally:
                signal.signal(signal.SIGTERM, saved)   # ハンドラを元へ戻す
            self.assertEqual(rc, 0)

    def test_watch_lock_is_released_on_exit(self):
        # 監視ロックは fd に紐づく。終了時に解放しないと、同じプロセスの再起動
        # （自己更新の execv）や次の起動が「既に監視中」と誤判定して上がれない。
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), watch=True)
            saved = signal.getsignal(signal.SIGTERM)
            try:
                with mock.patch.object(km, "run_watch", side_effect=KeyboardInterrupt):
                    self.assertEqual(km.cmd_run(cfg), 0)
            finally:
                signal.signal(signal.SIGTERM, saved)
            again = km.acquire_watch_lock(cfg)   # 解放されていれば取り直せる
            self.assertIsNotNone(again, "終了時にロックが解放されていない")
            km.release_watch_lock(again)


class TestResumeRun(unittest.TestCase):
    """resume-run: 停滞・失敗した run を『続きから』やり直す正規の口（viewer の再実行ボタン）。
    従来は viewer が backlog ファイルを直接書き換えており、分散構成では状態リポジトリへの
    第二の書き手＝コミット競合の源だった。"""

    @staticmethod
    def _write_meta(cfg, rid: str, status: str, lease: "float | None" = None) -> None:
        rd = cfg.bus / "runs" / rid
        rd.mkdir(parents=True, exist_ok=True)
        meta = {"status": status, "updated_at": "2026-01-01T00:00:00Z"}
        if lease is not None:
            meta["orch_lease_until"] = lease
        (rd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_resume_run_pins_last_run_and_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            rc = km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", "続きから")
            self.assertEqual(rc, 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "ready")
            self.assertEqual(t.get("last_run"), "req-x-T1-r0")
            # 次の act はこの run を再開する（run_id_for が last_run を採用する）
            self.assertEqual(km.run_id_for(cfg, t), "req-x-T1-r0")

    def test_resume_run_clears_feedback_so_resume_wins(self):
        # feedback / revised は「新しい run を作る」シグナル。人が『続きから』と明示したら外す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bd = d / "backlog"
            bd.mkdir(parents=True)
            (bd / "T1.md").write_text(
                "## T1: T1\n- status: blocked\n- verify: `true`\n- retries: 0\n"
                "- feedback: 前回の指示\n", encoding="utf-8")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", ""), 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertIsNone(t.get("feedback"))
            self.assertEqual(km.run_id_for(cfg, t), "req-x-T1-r0")

    def test_resume_run_rejects_live_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "running", lease=time.time() + 600)
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r0", ""), 2)

    def test_resume_run_allows_missing_run(self):
        # bus 掃除後でも ap/<task-id> ブランチから再開できるため、run 不在は拒否しない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self.assertEqual(km.cmd_resume_run(cfg, "T1", "req-x-T1-r9", ""), 0)

    def test_ingest_commands_resume_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked")
            cfg = cfg_for(d)
            self._write_meta(cfg, "req-x-T1-r0", "failed")
            cdir = d / "commands"
            cdir.mkdir()
            (cdir / "viewer-resume.json").write_text(json.dumps(
                {"command": "resume-run", "id": "T1", "run": "req-x-T1-r0",
                 "reason": "実行画面から再実行"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(cfg), ["resume-run:T1"])
            self.assertFalse((cdir / "viewer-resume.json").exists())
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.get("last_run"), "req-x-T1-r0")
            self.assertEqual(t.norm_status(), "ready")


class TestJournalRotation(unittest.TestCase):
    """journal.md のローテーション: 閾値超過で journal-archive/ へ退避し、保持世代を刈り込む。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.journal = self.tmp / "journal.md"

    def test_no_rotation_below_threshold(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 10_000):
            km.append_journal(self.journal, "small")
        self.assertFalse((self.tmp / "journal-archive").exists())

    def test_rotation_archives_and_starts_fresh(self):
        # 見るのは「退避しても行が失われない」ことだけ。保持世代の刈り込みは別テスト
        # （_JOURNAL_KEEP=0 で無制限）。両方を1つのテストで見ると、ローテーション行に
        # 載るアーカイブ名＝**ホスト名の長さ**で退避回数が変わり、保持世代 20 を超えるか
        # どうかが実行環境まかせになる（長いホスト名の機材でだけ落ちていた）。
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 200), \
             mock.patch.object(km, "_JOURNAL_KEEP", 0):
            for i in range(30):
                km.append_journal(self.journal, f"line {i} " + "x" * 40)
        arch = sorted((self.tmp / "journal-archive").iterdir())
        self.assertTrue(arch)                                   # 退避が発生している
        self.assertLess(self.journal.stat().st_size, 400)       # アクティブは小さいまま
        text = self.journal.read_text(encoding="utf-8")
        self.assertIn("journal をローテーション", text)          # 継続の目印を残す
        joined = "".join(p.read_text(encoding="utf-8") for p in arch) + text
        for i in range(30):
            self.assertIn(f"line {i} ", joined)                 # 行は失われない

    def test_rotation_prunes_old_archives(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 120), \
             mock.patch.object(km, "_JOURNAL_KEEP", 2):
            for i in range(60):
                km.append_journal(self.journal, f"line {i} " + "y" * 40)
        arch = [p for p in (self.tmp / "journal-archive").iterdir() if p.is_file()]
        self.assertLessEqual(len(arch), 2)                      # 保持世代で刈り込む
        # **どれが残るか**まで見る。同一秒に複数回退避すると連番が付くが、ゼロ詰めしないと
        # 名前順で ".10" が ".2" より前に並び、刈り込みが最古ではなく任意の世代を消して
        # journal の行が歯抜けに失われる。残るのは常に直近＝最も新しい行を含む世代。
        kept = "".join(p.read_text(encoding="utf-8") for p in arch)
        self.assertIn("line 59 ", kept + self.journal.read_text(encoding="utf-8"))
        self.assertNotIn("line 0 ", kept)                       # 最古が残り続けない
        newest = max(arch, key=lambda p: p.stat().st_mtime)
        others = [p for p in arch if p is not newest]
        for p in others:                                        # 残った世代は連続している
            self.assertLessEqual(p.stat().st_mtime, newest.stat().st_mtime)

    def test_rotation_disabled_with_zero(self):
        with mock.patch.object(km, "_JOURNAL_MAX_BYTES", 0):
            for i in range(50):
                km.append_journal(self.journal, "z" * 80)
        self.assertFalse((self.tmp / "journal-archive").exists())

    def test_build_config_sets_journal_globals(self):
        orig = (km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP)
        try:
            ns = types.SimpleNamespace(root=str(self.tmp), journal_max_bytes=99,
                                       journal_keep=3)
            km.resolve_config(ns)
            km.build_config(ns)
            self.assertEqual((km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP), (99, 3))
        finally:
            km._JOURNAL_MAX_BYTES, km._JOURNAL_KEEP = orig


class TestPauseResumeStop(unittest.TestCase):
    """commands/ のプロジェクト単位ライフサイクル指示（pause/resume/stop）。
    リモート viewer が git 越しに watch の消化を止め・再開し・プロセスを畳む口。"""

    def test_ingest_pause_then_resume(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "p.json").write_text('{"command": "pause", "reason": "検収中"}',
                                         encoding="utf-8")
            done = km.ingest_commands(cfg)
            self.assertIn("pause:project", done)
            self.assertTrue(km.is_paused(cfg))
            self.assertFalse((cdir / "p.json").exists())          # 消費済み
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(st["paused"])                          # 生存信号に paused が載る
            (cdir / "r.json").write_text('{"command": "resume"}', encoding="utf-8")
            km.ingest_commands(cfg)
            self.assertFalse(km.is_paused(cfg))
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertFalse(st["paused"])

    def test_ingest_heal_forces_state_sync_and_leaves_receipt(self):
        # commands/heal（設計 §5・実装計画 W2-5）: dashboard の 🩺 が投函する「今すぐ強制同期」。
        # 未知の指示のまま .err へ落ちると、押すたびに人が消す残骸が積む。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "viewer-heal-project.json").write_text(
                '{"command": "heal", "reason": "画面から強制同期"}', encoding="utf-8")
            forced = []
            orig = km.state_sync
            km.state_sync = lambda c, force=False: forced.append(force)
            try:
                done = km.ingest_commands(cfg)
            finally:
                km.state_sync = orig
            self.assertEqual(done, ["heal:project"])
            self.assertEqual(forced, [True])                       # force=True で押し出す
            self.assertFalse((cdir / "viewer-heal-project.json").exists())
            receipts = [p.name for p in km.commands_receipts_dir(cfg).glob("*.json")]
            self.assertEqual(receipts, ["viewer-heal-project.json"])

    def test_ingest_stop_raises_graceful(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            cdir = km.commands_dir(cfg)
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "s.json").write_text('{"command": "stop"}', encoding="utf-8")
            with self.assertRaises(km._StopRequested):
                km.ingest_commands(cfg)
            self.assertFalse((cdir / "s.json").exists())          # 再起動時に再停止しない

    def test_watch_skips_pass_while_paused(self):
        # paused の間は run_loop を起こさず、resume されたら消化を再開する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, watch=True)
            km.ensure_dirs(cfg)
            mkb(d, "T1")
            km.pause_path(cfg).write_text("{}", encoding="utf-8")

            def sleeper(_s):                                       # idle 1 回目で人が resume した体
                km.pause_path(cfg).unlink(missing_ok=True)

            last = km.run_watch(cfg, sleeper=sleeper, max_passes=1)
            self.assertEqual(last["reason"], km.REASON_DRAINED)    # resume 後に 1 パス回って消化
            self.assertEqual(last["counts"]["done"], 1)
            self.assertIn("一時停止中", cfg.journal.read_text(encoding="utf-8"))


class RunResumeTests(unittest.TestCase):
    """失敗した run は作り直さず再開する（失敗ノードだけやり直し、done は温存）。

    agent-flow は failed run を --run-id で受けると retry_failed を実行し、失敗ノードだけを
    pending へ戻して done のノードは温存する。ところが agent-project は --run-id を一切渡して
    いなかったため、リトライのたびにまっさらな run を作っていた。26 ノードのうち 1 つ失敗した
    だけで、成功していた 25 ノード分の LLM 呼び出しを丸ごと捨てて全部やり直していた。"""

    def _cfg(self, d):
        return cfg_for(Path(d))

    def _run(self, cfg, rid, status):
        p = cfg.bus / "runs" / rid
        p.mkdir(parents=True, exist_ok=True)
        (p / "meta.json").write_text(json.dumps({"status": status, "request": "x"}),
                                     encoding="utf-8")

    def test_failed_run_is_resumed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0", "同じ run を続きから")

    def test_stalled_run_is_resumed(self):
        # orchestrator が消えて status=running のまま止まった run（生存リース切れ）。
        # status だけを見ると救えず、失敗ノードも未実行ノードも永久に放置される。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            p = cfg.bus / "runs" / "req-deadbeef-T1-r0"
            p.mkdir(parents=True, exist_ok=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() - 60}), encoding="utf-8")
            self.assertEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0", "停滞 run は続きから")

    def test_live_run_is_not_resumed(self):
        # まだ実行中（リース有効）の run には触らない（走っているものを壊さない）
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            p = cfg.bus / "runs" / "req-deadbeef-T1-r0"
            p.mkdir(parents=True, exist_ok=True)
            (p / "meta.json").write_text(json.dumps({
                "status": "running", "orch_lease_until": time.time() + 600}), encoding="utf-8")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def _lease_less_run(self, cfg, rid, age_sec):
        """生存リースを持たない非終端 run（heartbeat を張る前に死んだ／旧版が残したもの）。"""
        p = cfg.bus / "runs" / rid
        p.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
        (p / "meta.json").write_text(json.dumps({
            "status": "running", "request": "x",
            "updated_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")

    def test_lease_less_stalled_run_is_resumed(self):
        # リース不在を「生きている」と読むと、進捗を抱えた run が永久に宙吊りになる。
        # 実際 agent-flow run（agent-project の主経路）は heartbeat を張っておらず、9/31 ノード
        # まで進んだ run が status=running のまま固まり、やり直す手段が無かった。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-20260712-213419-5922"))
            self._lease_less_run(cfg, "run-20260712-213419-5922", 2 * 3600)
            self.assertEqual(km.run_id_for(cfg, t), "run-20260712-213419-5922",
                             "リース未記録でも古ければ停滞＝続きから")

    def test_lease_less_fresh_run_is_not_resumed(self):
        # 起動直後（heartbeat を張る前）の run を停滞と誤読して奪わない
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "run-20260712-213419-5922"))
            self._lease_less_run(cfg, "run-20260712-213419-5922", 5)
            self.assertNotEqual(km.run_id_for(cfg, t), "run-20260712-213419-5922",
                                "走り出したばかりの run は触らない")

    def test_canceled_run_is_not_resumed(self):
        # 人が中止した＝その計画を続ける意図がない → 作り直す
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "cancelled")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_done_run_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            self._run(cfg, "req-deadbeef-T1-r0", "done")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_human_feedback_forces_a_fresh_run(self):
        # 人が差し戻した＝計画そのものが変わる → 続きからではなく作り直す
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            t.extra.append(("feedback", "方針を変えて"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_feedback_ingest_bumps_retries_so_id_diverges(self):
        # retries=0 + last_run=…-r0 の差し戻しでも新 id になる（ingest が retries を進める）。
        # 進めないと agent-flow が同じ id を再開し meta.request で差し戻しを捨てる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="review", verify="true", retries=0)
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            t0 = km.load_tasks(cfg.backlog)[0]
            t0.set("last_run", km._req_id_for(t0, cfg, 0))
            km.persist_task(cfg, t0)
            km.write_needs_file(cfg, t0, "検収", review=True)
            nf = d / "needs" / "T1.md"
            _submit_feedback(nf, "本番設定でやり直して")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.retries, 1)
            self.assertNotEqual(km.run_id_for(cfg, t), t.get("last_run"))

    def test_revise_forces_a_fresh_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true", retries=1)
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))
            t.extra.append(("revised", "1"))
            self._run(cfg, "req-deadbeef-T1-r0", "failed")
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_missing_run_falls_back_to_new(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true")
            t.extra.append(("last_run", "req-deadbeef-T1-r0"))   # bus に実体が無い
            self.assertNotEqual(km.run_id_for(cfg, t), "req-deadbeef-T1-r0")

    def test_new_run_id_carries_the_task_and_retry(self):
        # viewer が run ↔ タスクを突き合わせられる形（req-<hash>-<task-id>-r<n>）
        # 同期 run と daemon submit は同一導出（lineage が割れない）
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="TASK-9", title="x", status="ready", verify="true", retries=2)
            rid = km._new_run_id(t, cfg)
            self.assertTrue(rid.startswith("req-"))
            self.assertIn("TASK-9", rid)
            self.assertTrue(rid.endswith("-r2"))
            self.assertEqual(rid, km._req_id_for(t, cfg, 2), "同期 run と submit で同じ id")

    def test_cmd_passes_run_id_before_the_subcommand(self):
        # --run-id は agent-flow のグローバル引数（run サブコマンドより前）
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            t = km.Task(id="T1", title="x", status="ready", verify="true")
            cmd = km.build_agent_flow_cmd(t, cfg, run_id="req-x-T1-r0")
            self.assertIn("--run-id", cmd)
            self.assertLess(cmd.index("--run-id"), cmd.index("run"), "run より前に置く")
            self.assertEqual(cmd[cmd.index("--run-id") + 1], "req-x-T1-r0")
