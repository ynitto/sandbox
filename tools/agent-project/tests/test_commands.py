"""agent-project の単体テスト — commands（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）
from _shared import _drained, _submit_feedback  # noqa: E402,F401 — `import *` は _ 始まりを持ってこない


class TestFeedback(unittest.TestCase):
    def test_requires_checkbox(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            nf = d / "needs" / "T1.md"
            # 未チェックで本文だけ書いた（＝書きかけ）→ 取り込まれない
            nf.write_text(nf.read_text() + "\n書きかけのメモ\n", encoding="utf-8")
            self.assertEqual(km.ingest_feedback(cfg, km.load_tasks(d / "backlog")), [])

    def test_ingest_resumes_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d, actor="alice")
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "繰り返しNG")
            nf = d / "needs" / "T1.md"
            _submit_feedback(nf, "verify を直して再実行して")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(tasks[0].status, "ready")
            self.assertIn("feedback", dict(tasks[0].extra))
            self.assertFalse(nf.exists())
            self.assertTrue((d / "decisions" / "T1.md").exists())

    def test_run_loop_ingests_then_completes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            _submit_feedback(d / "needs" / "T1.md", "こう直して")
            res = km.run_loop(cfg)
            self.assertEqual(res["ingested"], ["T1"])
            self.assertEqual(res["counts"]["done"], 1)
            self.assertFalse((d / "backlog" / "T1.md").exists())


    def test_debounce_in_watch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d, watch=True, debounce=999)   # 直近編集は静穏化待ちで取り込まない
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            _submit_feedback(d / "needs" / "T1.md", "急いで保存した")
            self.assertEqual(km.ingest_feedback(cfg, km.load_tasks(d / "backlog")), [])

    def test_needs_is_madr_format(self):
        # needs/<id>.md は MADR 互換（frontmatter + Decision Outcome 欄）で生成され、
        # そのままフィードバック往復が成立する
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            km.write_needs_file(cfg, km.Task(id="T1", title="T1"), "NG")
            nf = d / "needs" / "T1.md"
            text = nf.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("status: proposed", text)
            self.assertIn("kind: blocked", text)
            self.assertIn("## Context and Problem Statement", text)
            self.assertIn(km.DECISION_MARKER, text)
            _submit_feedback(nf, "この方針で")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(dict(tasks[0].extra)["feedback"], "この方針で")

    def test_legacy_feedback_marker_still_ingested(self):
        # 旧形式（## フィードバック）の needs ファイルも引き続き取り込める
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            (d / "needs" / "T1.md").write_text(
                "# 要対応: T1\n\n## フィードバック\n- [x] 確定\n旧形式の方針\n",
                encoding="utf-8")
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            self.assertEqual(dict(tasks[0].extra)["feedback"], "旧形式の方針")


class TestCommandsIngest(unittest.TestCase):
    """指示のファイル取り込み（commands/*.json）。CLI と同一ロジックへの委譲・
    掃除・不正ファイルの退避・watch の起床を検証する。"""

    def test_ingest_commands_runs_cli_logic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d, actor="bob")
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "a.json").write_text(json.dumps(
                {"command": "approve", "id": "T1", "reason": "直した"}), encoding="utf-8")
            (cd / "b.json").write_text(json.dumps(
                {"command": "hold", "id": "T2", "reason": "本番は手動"}), encoding="utf-8")
            (cd / "c.json").write_text(json.dumps(
                {"command": "pin", "id": "T1"}), encoding="utf-8")
            done = km.ingest_commands(c)
            self.assertEqual(sorted(done), ["approve:T1", "hold:T2", "pin:T1"])
            self.assertEqual(list(cd.glob("*.json")), [])            # 処理したら消す
            t1 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T1")
            self.assertEqual(t1.status, "ready")                     # CLI approve と同じ効果
            self.assertIn("deny: T2", (d / "policy.md").read_text())
            self.assertIn("pin: T1", (d / "policy.md").read_text())
            self.assertIn("DR-", (d / "decisions" / "T1.md").read_text())  # 決定記録も同一

    def test_ingest_commands_revives_a_tombstone(self):
        # 却下（削除）の取り消しを viewer から届ける口。これが無いと、画面から消したタスクは
        # 墓標に阻まれて画面からは二度と入れ直せない（CLI が要る＝試行錯誤が止まる）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)
            km.ensure_dirs(c)
            km.append_tombstone(c, "README に概要を足す", "要らなかった")
            cd = km.commands_dir(c)
            (cd / "r.json").write_text(json.dumps(
                {"command": "revive", "title": "README に概要を足す"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["revive:project"])
            self.assertEqual(km.load_tombstones(c), [])              # 墓標が消える
            self.assertEqual(list(cd.glob("*.json")), [])            # 処理したら消す

    def test_ingest_commands_rejects_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "broken.json").write_text("{oops", encoding="utf-8")
            (cd / "unknown.json").write_text(json.dumps(
                {"command": "explode", "id": "T1"}), encoding="utf-8")
            (cd / "missing.json").write_text(json.dumps(
                {"command": "approve", "id": "NOPE"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), [])
            self.assertEqual(list(cd.glob("*.json")), [])            # 再試行ループにしない
            self.assertEqual(len(list(cd.glob("*.json.err"))), 3)    # .err に退避
            self.assertIn("commands 取り込み失敗", (d / "journal.md").read_text())

    def test_ingest_success_clears_stale_err_for_same_task(self):
        # .err は viewer の「直前の指示は失敗した」バナーの根拠。同じタスクへの指示が
        # 通ったら掃除しないと、解決済みの失敗が次の要対応カードに出続ける。
        # 他タスクの .err と JSON でないゴミは残す（失敗の履歴を巻き添えで消さない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "old1.json.err").write_text(json.dumps(
                {"error": "統合失敗", "failed_at": "2026-07-20 00:00:00",
                 "command": {"command": "approve", "id": "T1"}}), encoding="utf-8")
            (cd / "old2.json.err").write_text(json.dumps(
                {"error": "別タスク", "failed_at": "2026-07-20 00:00:00",
                 "command": {"command": "approve", "id": "T9"}}), encoding="utf-8")
            (cd / "garbage.json.err").write_text("{oops", encoding="utf-8")
            (cd / "a.json").write_text(json.dumps(
                {"command": "approve", "id": "T1", "reason": "直した"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["approve:T1"])
            self.assertEqual(sorted(p.name for p in cd.glob("*.err")),
                             ["garbage.json.err", "old2.json.err"])

    def test_ingest_commands_writes_success_receipt(self):
        # 成功した指示は processed/<name>.json に受理レシートを残す。viewer が元ファイル名で
        # 自分の「送信済み」表示を「受理済み」へ更新でき、押しても何も起きない停滞を排除する。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, actor="bob")
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "viewer-approve-x.json").write_text(json.dumps(
                {"command": "approve", "id": "T1", "reason": "直した"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["approve:T1"])
            self.assertEqual(list(cd.glob("*.json")), [])            # 元の指示は従来どおり消える
            receipts = list(km.commands_receipts_dir(c).glob("*.json"))
            self.assertEqual([p.name for p in receipts], ["viewer-approve-x.json"])
            rec = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertTrue(rec["ok"])
            self.assertEqual(rec["action"], "approve")
            self.assertEqual(rec["id"], "T1")
            self.assertEqual(rec["source"], "viewer-approve-x.json")

    def test_ingest_commands_failure_leaves_no_receipt(self):
        # 失敗は従来どおり .err にだけ残し、受理レシート（成功の痕跡）は書かない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)
            km.ensure_dirs(c)
            cd = km.commands_dir(c)
            (cd / "bad.json").write_text(json.dumps(
                {"command": "approve", "id": "NOPE"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), [])
            self.assertEqual(len(list(cd.glob("*.json.err"))), 1)
            rdir = km.commands_receipts_dir(c)
            self.assertFalse(rdir.exists() and list(rdir.glob("*.json")))

    def test_command_receipts_pruned_by_count(self):
        # 受理レシートは件数上限で掃除され、commands/ 履歴が肥大しない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)
            km.ensure_dirs(c)
            rdir = km.commands_receipts_dir(c)
            rdir.mkdir(parents=True, exist_ok=True)
            keep = km._RECEIPT_KEEP
            base = time.time() - 500                          # TTL 内かつ決定的な mtime 順
            for i in range(keep + 25):
                p = rdir / f"r{i:04d}.json"
                p.write_text("{}", encoding="utf-8")
                os.utime(p, (base + i, base + i))
            km._prune_command_receipts(c)
            remaining = sorted(p.name for p in rdir.glob("*.json"))
            self.assertEqual(len(remaining), keep)           # 上限まで削減
            self.assertNotIn("r0000.json", remaining)        # 最古が消える
            self.assertIn(f"r{keep + 24:04d}.json", remaining)  # 最新は残る

    def test_node_unnamed_engine_runs_everything(self):
        # 無名エンジン（node 未設定）は従来どおり全タスクを消化する（後方互換）。
        with tempfile.TemporaryDirectory() as d:
            c = cfg_for(Path(d))  # node="" 既定
            t_assigned = km.Task(id="T", title="T", status="ready", verify="true")
            t_assigned.set("node", "pcB")
            t_plain = km.Task(id="U", title="U", status="ready", verify="true")
            self.assertTrue(km.task_runnable_here(c, t_assigned))
            self.assertTrue(km.task_runnable_here(c, t_plain))

    def test_node_named_engine_honors_assignment(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg_for(Path(d), node="pcA")  # default_node="" 既定
            mine = km.Task(id="T", title="T", status="ready", verify="true"); mine.set("node", "pcA")
            others = km.Task(id="U", title="U", status="ready", verify="true"); others.set("node", "pcB")
            plain = km.Task(id="V", title="V", status="ready", verify="true")
            self.assertTrue(km.task_runnable_here(c, mine))      # 自ノード宛ては消化
            self.assertFalse(km.task_runnable_here(c, others))   # 他ノード宛ては消化しない
            self.assertTrue(km.task_runnable_here(c, plain))     # 未割当かつ default 空＝誰でも（従来）

    def test_node_default_funnels_unassigned(self):
        with tempfile.TemporaryDirectory() as d:
            cA = cfg_for(Path(d), node="pcA", default_node="pcA")
            cB = cfg_for(Path(d), node="pcB", default_node="pcA")
            plain = km.Task(id="T", title="T", status="ready", verify="true")
            self.assertTrue(km.task_runnable_here(cA, plain))    # default が未割当を拾う
            self.assertFalse(km.task_runnable_here(cB, plain))   # 非 default は未割当を拾わない
            assigned_b = km.Task(id="U", title="U", status="ready", verify="true"); assigned_b.set("node", "pcB")
            self.assertTrue(km.task_runnable_here(cB, assigned_b))  # 明示割当は default に依らず優先

    def test_node_has_work_skips_other_node_ready(self):
        # pcA エンジンは pcB 宛ての ready だけでは起きない（空パスの busy-loop 防止）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T", status="ready", verify="true")
            (d / "backlog" / "T.md").write_text(
                "## T: T\n- status: ready\n- source: human\n- verify: `true`\n"
                "- retries: 0\n- node: pcB\n", encoding="utf-8")
            c = cfg_for(d, node="pcA")
            self.assertFalse(km.has_work(c))
            # 自ノード宛てを足すと起きる
            mkb(d, "U", status="ready", verify="true")
            (d / "backlog" / "U.md").write_text(
                "## U: U\n- status: ready\n- source: human\n- verify: `true`\n"
                "- retries: 0\n- node: pcA\n", encoding="utf-8")
            self.assertTrue(km.has_work(c))

    def test_node_status_writes_per_node_file(self):
        # ノード名があれば status.json に加えて status/<node>.json を書く（複数 PC の生存一覧用）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, node="pc-A")
            km.ensure_dirs(c)
            km.write_status(c)
            shared = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(shared["node"], "pc-A")               # 単一 status にもノード名
            # ファイル名は板と同じ正規形（小文字・`normalize_node_id`）。ここに独自の
            # サニタイズを持っていたのが `status/DESKTOP-X.json` と `nodes/desktop-x.json` の
            # 2 名義の原因だった（P0-3）。実運用では build_config が cfg.node 自体を正規形へ
            # 倒すので、内容とファイル名の綴りは一致する。
            per = json.loads((d / "status" / "pc-a.json").read_text(encoding="utf-8"))
            self.assertEqual(per["node"], "pc-A")

    def test_node_status_no_per_node_file_when_unnamed(self):
        # 無名エンジンは従来どおり status.json のみ（status/ を作らない）。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)  # node="" 既定
            km.ensure_dirs(c)
            km.write_status(c)
            self.assertTrue((d / "status.json").exists())
            self.assertFalse((d / "status").exists())

    def test_node_enters_drain_window_in_its_local_timezone(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg_for(Path(d), availability={
                "timezone": "Asia/Tokyo", "daily_stop": "23:00", "drain_before_sec": 1800,
            })
            at = datetime(2026, 7, 22, 13, 45, tzinfo=timezone.utc)  # JST 22:45
            self.assertEqual(km.availability_state(c, at), "draining")

    def test_night_shutdown_grace_has_a_hard_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg_for(Path(d), availability={
                "timezone": "Asia/Tokyo", "daily_stop": "23:00", "drain_before_sec": 1800,
                "shutdown_grace_sec": 300,
            })
            self.assertFalse(km.shutdown_due(c, datetime(2026, 7, 22, 14, 4, 59, tzinfo=timezone.utc)))
            self.assertTrue(km.shutdown_due(c, datetime(2026, 7, 22, 14, 5, tzinfo=timezone.utc)))

    def test_node_revise_reassigns(self):
        # 監視者が revise で実行 PC（node）を付け替えられる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T", status="ready", verify="true")
            c = cfg_for(d, actor="watcher")
            self.assertEqual(km.cmd_revise(c, "T", {"node": "pcB"}, "", "担当変更"), 0)
            t = next(t for t in km.load_tasks(c.backlog) if t.id == "T")
            self.assertEqual(t.get("node"), "pcB")

    def test_has_work_wakes_on_commands(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")            # consumable 無し
            c = cfg_for(d)
            km.ensure_dirs(c)
            self.assertFalse(km.has_work(c))
            (km.commands_dir(c) / "a.json").write_text(json.dumps(
                {"command": "approve", "id": "T1"}), encoding="utf-8")
            self.assertTrue(km.has_work(c))                          # 指示ドロップで起きる

    def test_watch_ingests_readable_command_immediately(self):
        # 読める指示は watch 中でも即座に取り込む。debounce で先送りすると、has_work が起こした
        # パスで承認が処理されず、そのパスが charter を再評価してマイルストーンを書き直す
        # （承認したのに要対応が復活する）。viewer は .tmp → rename で置くので書きかけは読めない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, watch=True, debounce=999.0)
            km.ensure_dirs(c)
            f = km.commands_dir(c) / "a.json"
            f.write_text(json.dumps({"command": "approve", "id": "T1"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["approve:T1"])
            self.assertFalse(f.exists())                             # 処理したら消える

    def test_watch_debounce_defers_unreadable_command(self):
        # 書きかけ（アトミックに置かれなかった指示）は .err へ飛ばさず静穏化を待つ。
        # 猶予中は has_work も起こさない＝起きたパスは必ずその指示を処理できる。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d, watch=True, debounce=999.0)
            km.ensure_dirs(c)
            f = km.commands_dir(c) / "a.json"
            f.write_text('{"command": "appr', encoding="utf-8")      # 書きかけ
            self.assertEqual(km.ingest_commands(c), [])
            self.assertTrue(f.exists())                              # .err にしない（指示を失わない）
            self.assertFalse(km.has_work(c))                         # 読めない指示では起こさない

    def test_approve_drop_does_not_resurrect_milestone(self):
        # 実運用インシデントの再発防止: viewer の「プロジェクトを承認」を押すと commands/ に
        # 指示が落ち、has_work がその場で watch を起こす。かつては ingest_commands が debounce
        # 未経過のその指示を読み飛ばしたため、承認を知らないまま cmd_project が再評価して
        # converged → write_milestone となり、承認直後に「要対応: マイルストーン」が復活していた。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, CHARTER.replace("test -f {flag}", 'echo "hellO"'))
            c = cfg_for(d, watch=True, debounce=999.0, max_project_cycles=1)
            km.cmd_project(c, runner=lambda c2: _drained())          # 収束 → milestone が出る
            st = km.load_project_state(c)
            pid = st["id"]
            self.assertEqual(st["status"], km.REASON_PROJECT_CONVERGED)
            self.assertTrue(km.needs_path(c, pid).exists())

            km.commands_dir(c).mkdir(parents=True, exist_ok=True)    # viewer の承認ドロップ
            (km.commands_dir(c) / "viewer-approve.json").write_text(json.dumps(
                {"command": "approve", "id": pid, "reason": "viewer から"}), encoding="utf-8")
            self.assertTrue(km.has_work(c))                          # 置いた直後に watch が起きる

            km.cmd_project(c, runner=lambda c2: _drained())          # その起床パス
            self.assertEqual(km.load_project_state(c)["status"], km.REASON_PROJECT_ACCEPTED)
            self.assertFalse(km.needs_path(c, pid).exists())         # マイルストーンは復活しない


class TestStatusHeartbeat(unittest.TestCase):
    """リモート agent-dashboard 向けの生存信号（status.json）。idle 中は既定で
    state_git への追加コミットを一切生まないこと（--status-interval は opt-in）を検証する。"""

    def test_write_status_content(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, watch=True, level="assisted", state_git_interval=300.0)
            km.write_status(c)
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(rec["watch"])
            self.assertEqual(rec["level"], "assisted")
            self.assertIn("updated_iso", rec)
            self.assertEqual(rec["fresh_after_sec"], 600.0)          # 2 * state_git_interval
            self.assertIn("runtime", rec)                            # Windows×WSL 同一マシン判定用
            self.assertIn(rec["runtime"], ("linux", "wsl", "windows", "darwin"))

    def test_fresh_after_sec_floor_and_max(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # 両方 0（未設定）でもフロア 120 秒を下回らない
            c0 = cfg_for(d, state_git_interval=0.0, status_interval=0.0)
            self.assertEqual(km._status_fresh_after_sec(c0), 120.0)
            # 大きい方（status_interval）が勝つ
            c1 = cfg_for(d, state_git_interval=300.0, status_interval=1000.0)
            self.assertEqual(km._status_fresh_after_sec(c1), 2000.0)

    def test_maybe_heartbeat_disabled_by_default_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, status_interval=0.0)                       # 既定 0 = 無効
            km.maybe_heartbeat_status(c)
            self.assertFalse((d / "status.json").exists())            # idle 中の追加コミット元を作らない

    def test_maybe_heartbeat_enabled_throttles_to_interval(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d, status_interval=100.0)
            km.maybe_heartbeat_status(c)                              # 未作成 → 書く
            self.assertTrue((d / "status.json").exists())
            first_mtime = (d / "status.json").stat().st_mtime
            km.maybe_heartbeat_status(c)                              # 直後の再呼び出しは間隔未満 → 書かない
            self.assertEqual((d / "status.json").stat().st_mtime, first_mtime)
            # 間隔を過ぎたことにする（mtime を過去へ）
            old = time.time() - 101.0
            os.utime(d / "status.json", (old, old))
            km.maybe_heartbeat_status(c)
            self.assertGreater((d / "status.json").stat().st_mtime, old)

    def test_run_loop_piggybacks_status_write(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, level="assisted")
            km.ensure_dirs(c)
            km.run_loop(c, act=lambda t, cfg, loc: (True, "ok"))
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["level"], "assisted")
            self.assertTrue(rec["watch"] is False)                    # cfg_for 既定は watch=False

    def test_throttle_demotion_refreshes_status(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False,
                       watch=True, max_tokens=100, throttle=0.5)

            def act(t, cfg, loc):
                t.extra.append(("_cost_marker", "1"))
                return (True, "ok @cost tokens=80")

            km.run_watch(c, act=act, sleeper=lambda s: None, max_passes=1)
            rec = json.loads((d / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(rec["level"], "report")                  # 降格後の値で上書きされている


class TestRevise(unittest.TestCase):
    """人の即時フィードバック（revise）。内容・依存 after の修正と feedback 注入、
    実行中タスクの積み直し予約（revised マーカー）、CLI/commands ドロップの同一実装を検証する。"""

    def test_revise_updates_fields_deps_and_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d, actor="alice")
            km.ensure_dirs(c)
            rc = km.cmd_revise(c, "T2", {"title": "実サーバで e2e", "priority": 5, "after": "T1"},
                               "ローカルサーバでなく実サーバに配備して e2e を実施すること", "軌道修正")
            self.assertEqual(rc, 0)
            t2 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T2")
            self.assertEqual(t2.title, "実サーバで e2e")
            self.assertEqual(t2.priority, 5)
            self.assertEqual(km.task_deps(t2), ["T1"])
            self.assertIn("実サーバに配備", t2.feedback())
            self.assertEqual(t2.get("rev"), "1")                     # act 試行の世代番号
            self.assertEqual(t2.status, "ready")                     # 状態は変えない
            drs = (d / "decisions" / "T2.md").read_text(encoding="utf-8")
            self.assertIn("action  : revise", drs)                   # 決定記録
            self.assertIn("- learn:", drs)                           # feedback は学習材料にも
            # 依存が効く: T2 は T1 が残る間は選ばれない
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual([t.id for t in km.ready_after_deps(tasks)], ["T1"])

    def test_revise_validates_input(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            self.assertEqual(km.cmd_revise(c, "NOPE", {"title": "x"}, "", ""), 2)   # 不在
            self.assertEqual(km.cmd_revise(c, "T1", {}, "", ""), 2)                 # 変更なし
            self.assertEqual(km.cmd_revise(c, "T1", {"level": "bogus"}, "", ""), 2)  # level 不正
            self.assertEqual(km.cmd_revise(c, "T1", {"after": "T1"}, "", ""), 2)     # 自己依存
            # 循環（T1 after T2, T2 after T1）は拒否し、ファイルは変えない
            mkb(d, "T2", verify="true")
            self.assertEqual(km.cmd_revise(c, "T2", {"after": "T1"}, "", ""), 0)
            self.assertEqual(km.cmd_revise(c, "T1", {"after": "T2"}, "", ""), 2)
            t1 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T1")
            self.assertEqual(km.task_deps(t1), [])

    def test_revise_clears_fields(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            km.cmd_revise(c, "T2", {"after": "T1", "note": "旧メモ"}, "", "")
            km.cmd_revise(c, "T2", {"after": "none", "note": ""}, "", "")
            t2 = next(t for t in km.load_tasks(d / "backlog") if t.id == "T2")
            self.assertEqual(km.task_deps(t2), [])
            self.assertIsNone(t2.get("note"))

    def test_revise_blocked_requeues_and_clears_needs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            t = km.load_tasks(d / "backlog")[0]
            km.write_needs_file(c, t, "検証 NG")
            rc = km.cmd_revise(c, "T1", {"verify": "test -f ok.txt"}, "ok.txt を作る方式にする", "")
            self.assertEqual(rc, 0)
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.status, "ready")                     # 積み直し（needs 記入と同じ復帰）
            self.assertEqual(t1.verify, "test -f ok.txt")
            self.assertFalse((d / "needs" / "T1.md").exists())

    def test_verify_only_revise_reuses_done_run_without_rebuilding_graph(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="false")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False)
            km.ensure_dirs(c)
            task_file = d / "backlog" / "T1.md"
            task_file.write_text(
                task_file.read_text(encoding="utf-8") + "- last_run: run-done\n- rev: 4\n",
                encoding="utf-8",
            )
            run_dir = c.bus / "runs" / "run-done"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "done", "request": "original"}), encoding="utf-8"
            )
            t = km.load_tasks(d / "backlog")[0]
            km.write_needs_file(c, t, "verify NG")

            self.assertEqual(km.cmd_revise(c, "T1", {"verify": "true"}, "", ""), 0)
            revised = km.load_tasks(d / "backlog")[0]
            self.assertEqual(revised.get("rev"), "4")
            self.assertEqual(revised.get("reuse_done_run"), "run-done")

            calls = []
            km.run_loop(c, act=lambda *_: calls.append("act") or (True, "unexpected"))

            self.assertEqual(calls, [])
            self.assertEqual(list((d / "backlog").glob("*.md")), [])
            self.assertIn("run-done の成果を再利用", (d / "journal.md").read_text(encoding="utf-8"))

    def test_verify_only_revise_does_not_reuse_unfinished_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="blocked", verify="false")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False)
            km.ensure_dirs(c)
            task_file = d / "backlog" / "T1.md"
            task_file.write_text(
                task_file.read_text(encoding="utf-8") + "- last_run: run-active\n- rev: 4\n",
                encoding="utf-8",
            )
            run_dir = c.bus / "runs" / "run-active"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "running", "request": "original"}), encoding="utf-8"
            )

            self.assertEqual(km.cmd_revise(c, "T1", {"verify": "true"}, "", ""), 0)
            revised = km.load_tasks(d / "backlog")[0]
            self.assertEqual(revised.get("rev"), "5")
            self.assertIsNone(revised.get("reuse_done_run"))

            calls = []
            km.run_loop(c, act=lambda *_: calls.append("act") or (True, "new run"))
            self.assertEqual(calls, ["act"])

    def test_ingest_commands_revise(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            (km.commands_dir(c) / "r.json").write_text(json.dumps(
                {"command": "revise", "id": "T1", "priority": 9,
                 "feedback": "実サーバで e2e", "reason": "軌道修正"}), encoding="utf-8")
            self.assertEqual(km.ingest_commands(c), ["revise:T1"])
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.priority, 9)
            self.assertIn("実サーバ", t1.feedback())

    def test_claim_adopts_disk_edits(self):
        # パス途中の CLI revise / 直接編集が、doing 永続化で上書き消失しないこと
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            stale = km.load_tasks(d / "backlog")[0]                  # パス開始時点の in-memory 相当
            km.cmd_revise(c, "T1", {"priority": 7}, "最新の指示", "")  # その後の人の修正
            self.assertTrue(km.claim_task(c, stale))
            self.assertEqual(stale.priority, 7)                      # ディスク内容を採用
            self.assertIn("最新の指示", stale.feedback())

    def test_run_id_changes_with_rev(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            c = cfg_for(d)
            t = km.Task(id="T1", title="x")
            base = km._new_run_id(t, c)
            t.set("rev", "1")
            self.assertNotEqual(base, km._new_run_id(t, c))       # 世代が上がれば新しい run
            self.assertTrue(km._new_run_id(t, c).endswith("-v1"))

    def test_revise_during_act_requeues_without_settling(self):
        # 実行中の revise: 現在の試行は verify=PASS 相当でも確定せず、修正内容で再実行される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, max_cycles=10)
            km.ensure_dirs(c)
            seen = []

            def act(t, cfg, loc):
                seen.append(t.feedback())
                if len(seen) == 1:      # 人が act 中に気づいて revise した想定（別プロセス相当）
                    rc = km.cmd_revise(cfg, "T1", {"title": "実サーバ e2e"},
                                       "ローカルサーバでなく実サーバに配備して実施", "軌道修正")
                    assert rc == 0
                return (True, "ok")

            res = km.run_loop(c, act=act)
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertEqual(len(seen), 2)                           # 積み直し → 再実行
            self.assertIsNone(seen[0])
            self.assertIn("実サーバに配備", seen[1])                  # 修正が次 act に届いた
            self.assertIn("revise により積み直し", (d / "journal.md").read_text(encoding="utf-8"))
            self.assertEqual(list((d / "backlog").glob("*.md")), []) # 2回目で done

    def test_revise_dead_owner_doing_requeues_immediately(self):
        # 同一ホストの死んだ pid クレームは TTL を待たず「実行者不在」扱い → ready へ即積み直し。
        # TTL 専用だと claim TTL が切れるまで revised 予約だけして進まない。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            lock = d / "claims" / "T1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({
                "host": socket.gethostname(), "pid": dead.pid,
                "ts": time.time(), "id": "T1",
            }), encoding="utf-8")
            rc = km.cmd_revise(c, "T1", {"title": "即やり直し"}, "owner 失踪", "")
            self.assertEqual(rc, 0)
            t = km.load_tasks(c.backlog)[0]
            self.assertEqual(t.status, "ready")
            self.assertIsNone(t.get("revised"))
            self.assertEqual(t.title, "即やり直し")
            self.assertFalse(lock.exists())

    def test_revise_offloaded_detaches_and_requeues(self):
        # 委譲中の revise: 旧 run を cancel して切り離し、ready へ（二重書き込み防止）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: offloaded\n- verify: true\n"
                "- flow_run: run-old\n- flow_loc: daemon\n- rev: 0\n",
                encoding="utf-8")
            c = cfg_for(d)
            km.ensure_dirs(c)
            run_dir = c.bus / "runs" / "run-old"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "running", "request": "x"}), encoding="utf-8")
            (run_dir / "waits").mkdir()
            (run_dir / "waits" / "n1.json").write_text("{}", encoding="utf-8")
            rc = km.cmd_revise(c, "T1", {"title": "方針変更"}, "委譲中に修正", "")
            self.assertEqual(rc, 0)
            t = km.load_tasks(c.backlog)[0]
            self.assertEqual(t.status, "ready")
            self.assertIsNone(t.get("flow_run"))
            self.assertEqual(str(t.get("rev")), "1")
            cancel = c.bus / "inbox" / "cancels" / "run-old.json"
            self.assertFalse(cancel.is_file(), "適用後 sticky cancel は残さない")
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "cancelled")
            self.assertFalse((run_dir / "waits" / "n1.json").exists())

    def test_revise_doing_with_flow_run_detaches(self):
        """dashboard cancel→revise: sync 待ち doing＋flow_run でも detach（approve と同契約）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: doing\n- verify: true\n"
                "- flow_run: run-sync\n- flow_loc: local\n- rev: 0\n- retries: 0\n",
                encoding="utf-8")
            c = cfg_for(d)
            km.ensure_dirs(c)
            # 新鮮クレーム＝実行中 doing。これで revised 経路に入りつつ flow_run detach も走る。
            claim = d / "claims" / "T1.lock"
            claim.parent.mkdir(parents=True, exist_ok=True)
            claim.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                                         "ts": time.time(), "id": "T1"}), encoding="utf-8")
            run_dir = c.bus / "runs" / "run-sync"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "running", "request": "x"}), encoding="utf-8")
            rc = km.cmd_revise(c, "T1", {}, "cancel sync", "agent-dashboard が run をキャンセル")
            self.assertEqual(rc, 0)
            t = km.load_tasks(c.backlog)[0]
            self.assertIsNone(t.get("flow_run"), "flow_run を外す")
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "cancelled")
            # fresh doing → revised 予約も残る（settle で積み直し）
            self.assertIsNotNone(t.get("revised"))

    def test_approve_offloaded_detaches_and_requeues(self):
        # 委譲中の approve（CLI/commands）: flow を止めてから ready（二重書き込み防止）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: offloaded\n- verify: true\n"
                "- flow_run: run-old\n- flow_loc: daemon\n- retries: 0\n",
                encoding="utf-8")
            c = cfg_for(d, learn=False)
            km.ensure_dirs(c)
            run_dir = c.bus / "runs" / "run-old"
            run_dir.mkdir(parents=True)
            (run_dir / "meta.json").write_text(
                json.dumps({"status": "running", "request": "x"}), encoding="utf-8")
            self.assertEqual(km.cmd_approve(c, "T1", "委譲中だが進める"), 0)
            t = km.load_tasks(c.backlog)[0]
            self.assertEqual(t.status, "ready")
            self.assertIsNone(t.get("flow_run"))
            self.assertEqual(t.retries, 1)
            self.assertFalse((c.bus / "inbox" / "cancels" / "run-old.json").is_file())
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "cancelled")

    def test_midpass_command_applies_before_next_task(self):
        # パス途中の commands/ ドロップが、後続タスクの実行前に取り込まれること
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="true")
            (d / "backlog" / "T2.md").write_text(
                "## T2: 後続\n- status: ready\n- verify: `true`\n- priority: -1\n",
                encoding="utf-8")
            c = cfg_for(d, dry_run=False, learn=False, auto_adjudicate=False, max_cycles=10)
            km.ensure_dirs(c)
            seen = {}

            def act(t, cfg, loc):
                if t.id == "T1":        # T1 実行中に人が T2 へ指示を落とした想定
                    (km.commands_dir(cfg) / "r.json").write_text(json.dumps(
                        {"command": "revise", "id": "T2",
                         "feedback": "実サーバで e2e"}), encoding="utf-8")
                seen[t.id] = t.feedback()
                return (True, "ok")

            res = km.run_loop(c, act=act)
            self.assertEqual(res["reason"], km.REASON_DRAINED)
            self.assertIn("実サーバ", seen["T2"] or "")               # 次サイクル開始時に反映済み

    def test_recover_revised_requeues_orphan(self):
        # 実行者不在（stale claim）の revised マーカーは自己回復で ready に戻す
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing", verify="true")
            c = cfg_for(d)
            km.ensure_dirs(c)
            tasks = km.load_tasks(d / "backlog")
            tasks[0].set("revised", "2026-01-01 00:00:00")
            km.persist_task(c, tasks[0])
            tasks = km.load_tasks(d / "backlog")
            self.assertEqual(km.recover_revised(c, tasks), ["T1"])
            t1 = km.load_tasks(d / "backlog")[0]
            self.assertEqual(t1.status, "ready")
            self.assertIsNone(t1.get("revised"))


class TestFailureTriage(unittest.TestCase):
    """失敗トリアージ: 環境要因（quota/auth/env）はタスクの内容と無関係 —
    リトライを焼かず・裁定も呼ばず、原因と直し方を明記して人へ回す。"""

    def test_classify_and_tag(self):
        cls, _ = km.classify_agent_failure("codex error: usage limit reached")
        self.assertEqual(cls, "quota")
        msg = km._agent_failure("codex", 1, "", "usage limit reached")
        self.assertTrue(msg.startswith("[agent-error:quota]"), msg)
        # 既にタグ付き（agent-flow 経由）ならタグが正
        cls, _ = km.classify_agent_failure("[agent-error:auth] なにか")
        self.assertEqual(cls, "auth")
        # 内容の問題（該当なし）は None
        self.assertIsNone(km.classify_agent_failure("テストが 3 件落ちました"))

    def test_source_marker_beats_stale_tag(self):
        """発生元マーカーはタグより強い。

        タグを無条件に正とすると、内側で付いた分類を外側から上書きできない。実際
        [agent-control] による停止が quota タグを載せたまま運ばれ、画面は「利用上限です。
        時間をおいてください」と表示した——必要な操作は「実行を run に戻す」で、待っても
        永久に回復しない。生の本文に残るマーカーは後から見ても正しいので、それを先に見る。"""
        stale = ("[agent-error:quota] [agent-control] このワークロード（flow）は管理面により "
                 "lifecycle=stop 指定です")
        cls, hint = km.classify_agent_failure(stale)
        self.assertEqual(cls, "control")
        self.assertIn("dashboard", hint)
        # node-budget（このノードの予算超過）は quota のまま
        self.assertEqual(
            km.classify_agent_failure("[node-budget] このノードのトークン予算を超過しています")[0],
            "quota")
        # マーカーが無ければ従来どおりタグが正
        self.assertEqual(km.classify_agent_failure("[agent-error:auth] なにか")[0], "auth")

    def test_error_chain_keeps_every_observed_class(self):
        """観測した分類を先頭以外も残す。

        先頭（proximate cause）だけ保存すると、分類器が後で直っても保存済みの記録は
        誤ったままになる。実際 quota タグと [agent-control] マーカーが同居した記録で、
        捨てた側が正しかった。根拠として全部を持ち、表示は先頭を使う。"""
        stale = ("[agent-error:quota] [agent-control] このワークロード（flow）は管理面により "
                 "lifecycle=stop 指定です")
        self.assertEqual(km.agent_error_chain(stale), ["control", "quota"])
        self.assertEqual(km.classify_agent_failure(stale)[0], "control")
        # 単一分類は 1 要素、該当なしは空
        self.assertEqual(km.agent_error_chain("[agent-error:auth] なにか"), ["auth"])
        self.assertEqual(km.agent_error_chain("テストが 3 件落ちました"), [])
        # パターン一致（タグ無し）も拾う
        self.assertEqual(km.agent_error_chain("codex error: usage limit reached"), ["quota"])

    def test_settle_failure_env_class_blocks_without_burning_retries(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing")
            cfg = cfg_for(d)
            task = km.load_tasks(cfg.backlog)[0]
            km._settle_failure(cfg, task, "[agent-error:auth] kiro-cli 失敗 (rc=0): 認証切れ",
                               1, "", {})
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "blocked")       # 人へ（環境を直すまで回さない）
            self.assertEqual(t.retries, 0)                     # リトライを焼かない
            needs = list((d / "needs").glob("T1.md"))
            self.assertEqual(len(needs), 1)
            body = needs[0].read_text(encoding="utf-8")
            self.assertIn("認証", body)                         # 何を直すかが書いてある
            self.assertIn("続き", body)                         # 直せば続きから、と言い切る
            self.assertEqual(t.get("env_resume"), "1")

    def test_env_resume_survives_memo_feedback(self):
        # 環境ブロック後に needs へ「直した」と書いて [x] しても同 run 再開（計画変更ではない）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "backlog").mkdir()
            (d / "backlog" / "T1.md").write_text(
                "## T1: x\n- status: blocked\n- verify: `true`\n"
                "- last_run: run-env\n- env_resume: 1\n- retries: 0\n",
                encoding="utf-8")
            cfg = cfg_for(d)
            km.ensure_dirs(cfg)
            (cfg.bus / "runs" / "run-env").mkdir(parents=True)
            (cfg.bus / "runs" / "run-env" / "meta.json").write_text(
                json.dumps({"status": "failed", "updated_at": "2026-07-01T00:00:00Z"}),
                encoding="utf-8")
            t0 = km.load_tasks(cfg.backlog)[0]
            km.write_needs_file(cfg, t0, "[agent-error:auth] 認証切れ")
            nf = d / "needs" / "T1.md"
            _submit_feedback(nf, "トークンを入れ直した")
            tasks = km.load_tasks(cfg.backlog)
            self.assertEqual(km.ingest_feedback(cfg, tasks), ["T1"])
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.retries, 0)
            self.assertEqual(t.get("env_resume"), "1")
            self.assertEqual(km.run_id_for(cfg, t), "run-env")

    def test_feedback_submitted_ignores_body_checkbox(self):
        # 本文のチェックリスト [x] だけでは確定扱いにしない（Decision Outcome 配下だけ）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            nf = Path(d) / "n.md"
            nf.write_text(
                "# 要対応\n\n- [x] 手順1は済\n\n## Decision Outcome\n\n- [ ] 確定\n",
                encoding="utf-8")
            self.assertFalse(km.feedback_submitted(nf))
            nf.write_text(
                "# 要対応\n\n- [x] 手順1は済\n\n## Decision Outcome\n\n- [x] 確定\n",
                encoding="utf-8")
            self.assertTrue(km.feedback_submitted(nf))

    def test_settle_failure_content_class_retries_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="doing")
            cfg = cfg_for(d)
            task = km.load_tasks(cfg.backlog)[0]
            km._settle_failure(cfg, task, "verify NG: テストが落ちた", 1, "", {})
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "ready")         # 内容の問題 → 従来どおり積み直し
            self.assertEqual(t.retries, 1)


class FeedbackReductionTests(unittest.TestCase):
    """ユーザーの決定・指摘を全体へ還元する仕組み（gitlab 却下コメントの learn 化・蒸留）と
    verify 品質改善（恒真式スクリーン・テンプレ拡充）。"""

    def test_distill_learn_generalizes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            got = km.distill_learn(cfg, "ログイン画面の e2e",
                                   "実サーバでなく localhost で検証していてダメ",
                                   agent_run=lambda p, m: "e2e/統合テスト系 :: 実サーバ配備で実施すること")
            self.assertEqual(got, ("e2e/統合テスト系", "実サーバ配備で実施すること"))

    def test_distill_learn_verbatim_fallback_on_error(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            def boom(p, m): raise RuntimeError("no kiro-cli")
            title, guide = km.distill_learn(cfg, "T", "実サーバで検証", agent_run=boom)
            self.assertEqual(title, "T")
            self.assertIn("実サーバで検証", guide)

    def test_distill_learn_off_returns_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d), distill_learn=False)
            got = km.distill_learn(cfg, "T", "生の指摘",
                                   agent_run=lambda p, m: self.fail("蒸留された"))
            self.assertEqual(got, ("T", "生の指摘"))

    def test_expand_verify_template_additions(self):
        self.assertEqual(km.expand_verify_template("test-passes :: pytest -q"), "pytest -q")
        self.assertEqual(km.expand_verify_template("builds :: make"), "make")
        self.assertEqual(km.expand_verify_template("exit-zero :: ./run.sh"), "./run.sh")
        cmd = km.expand_verify_template("endpoint-returns :: http://x/health :: 200")
        self.assertIn("http_code", cmd)
        self.assertIn("200", cmd)
        self.assertIn("http://x/health", cmd)

    def test_reject_guidance_captured_as_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", verify="pytest -q", title="ログイン e2e")
            cfg = cfg_for(d, executor="gitlab")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance",
                                   return_value="実サーバで検証すること"), \
                 mock.patch.object(km, "distill_learn",
                                   return_value=("e2e 系", "実サーバ配備で実施")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            dr = (cfg.decisions / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- learn: e2e 系 :: 実サーバ配備で実施", dr)
            self.assertIn("gitlab-reject", dr)

    def test_reject_learn_suppressed_when_capture_off(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T2", verify="pytest -q", title="x")
            cfg = cfg_for(d, executor="gitlab", learn_capture=False)
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="直して"):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertFalse((cfg.decisions / "T2.md").exists())

    def test_approve_notes_captured_as_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T3", verify="true", title="ログイン e2e")
            cfg = cfg_for(d, executor="gitlab")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_result_notes",
                                   return_value=[{"body": "実サーバで検証してOK", "note_id": 1}]), \
                 mock.patch.object(km, "distill_learn",
                                   return_value=("e2e 系", "実サーバ配備で実施")):
                km.capture_approve_learn(cfg, task, "local")
            dr = (cfg.decisions / "T3.md").read_text(encoding="utf-8")
            self.assertIn("gitlab-approve", dr)
            self.assertIn("- learn: e2e 系 :: 実サーバ配備で実施", dr)

    def test_build_request_injects_similar_learn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            (cfg.decisions / "old.md").write_text(
                "## DR1 2026-01-01 actor: gitlab\n- learn: ログイン e2e :: 実サーバ配備で検証すること\n\n",
                encoding="utf-8")
            task = km.Task(id="NEW", title="ログイン e2e を追加", verify="pytest -q")
            req = km.build_request(task, cfg)
            self.assertIn("類似タスクでの学び", req)
            self.assertIn("実サーバ配備で検証すること", req)   # 分解・実装へ届く

    def test_cohort_reflux_propagates_to_siblings(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            # 同 cohort の 3 メンバ（ready）に cohort タグを付与
            for tid in ("M1", "M2", "M3"):
                mkb(d, tid, status="ready", verify="true", title=f"{tid} 移行")
            for tid in ("M1", "M2", "M3"):
                t = [x for x in km.load_tasks(d / "backlog") if x.id == tid][0]
                t.set("cohort", "C1")
                km.persist_task(cfg, t)
            m1 = [x for x in km.load_tasks(d / "backlog") if x.id == "M1"][0]
            n = km.cohort_reflux(cfg, m1, "パスの命名は kebab-case に統一")
            self.assertEqual(n, 2)                              # M2/M3 に波及（M1 自身は除く）
            for tid in ("M2", "M3"):
                t = [x for x in km.load_tasks(d / "backlog") if x.id == tid][0]
                self.assertIn("kebab-case", t.feedback())

    def test_cohort_reflux_noop_for_non_cohort(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "X", status="ready", title="単発")
            x = km.load_tasks(d / "backlog")[0]
            self.assertEqual(km.cohort_reflux(cfg, x, "指摘"), 0)

    def _seed_reject_decision(self, cfg, tid, title):
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        (cfg.decisions / f"{tid}.md").write_text(
            f"## DR-0001  2026-01-01  actor: gitlab\n"
            f"- context : {tid}（{title}）が gitlab で却下\n- action  : gitlab-reject\n"
            f"- reason  : x\n- affects : {tid}\n- learn: e2e 系 :: 実サーバで\n\n", encoding="utf-8")

    def test_count_gitlab_reject_recur(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")
            self._seed_reject_decision(cfg, "B", "無関係な掃除タスク")
            task = km.Task(id="C", title="ログイン e2e C")
            self.assertEqual(km.count_gitlab_reject_recur(cfg, task), 1)  # A のみ類似

    def test_reject_recurrence_escalates_to_human(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "C", verify="pytest -q", title="ログイン e2e C")
            cfg = cfg_for(d, executor="gitlab", reject_recur=2)
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")  # 既に 1 件の同種却下
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="また命名が違う"), \
                 mock.patch.object(km, "distill_learn", return_value=("e2e 系", "実サーバで")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertEqual(task.norm_status(), "blocked")            # 系の再考で人へ
            self.assertTrue((d / "needs" / "C.md").exists())

    def test_reject_recurrence_disabled_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "C", verify="pytest -q", title="ログイン e2e C")
            cfg = cfg_for(d, executor="gitlab", reject_recur=0)     # 無効
            self._seed_reject_decision(cfg, "A", "ログイン e2e A")
            task = km.load_tasks(d / "backlog")[0]
            with mock.patch.object(km, "executor_delegates", return_value=True), \
                 mock.patch.object(km, "read_reject_guidance", return_value="直して"), \
                 mock.patch.object(km, "distill_learn", return_value=("t", "g")):
                km._settle_failure(cfg, task, "NG", 1, "ev", {}, location="local")
            self.assertEqual(task.status, "ready")                    # silent 積み直し

    # --- red-green（変更を弁別しない合成 verify を実行で弾く）---
    def _git_repo(self, d: Path, fname="f", content="old"):
        import subprocess as sp
        sp.run(["git", "init", "-q", str(d)], check=True)
        sp.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
        sp.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
        (d / fname).write_text(content)
        sp.run(["git", "-C", str(d), "add", "-A"], check=True)
        sp.run(["git", "-C", str(d), "commit", "-qm", "base"], check=True)
        return km._git_out(d, "rev-parse", "HEAD").strip()

    def test_redgreen_passes_for_discriminating_verify(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            (d / "f").write_text("new")                 # act 後の作業ツリー
            cfg = cfg_for(d, workdir=d)
            task = km.Task(id="T", title="x", verify="grep -q new f")
            task.extra.append(("verify_source", "synth"))
            # base では 'new' が無い＝fail、post では pass ⇒ 弁別している＝undiscriminating False
            self.assertFalse(km.verify_undiscriminating(cfg, task, d, False,
                                                        (base, frozenset()), None))

    def test_redgreen_flags_stale_verify(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            (d / "f").write_text("old changed but still has old")
            cfg = cfg_for(d, workdir=d)
            task = km.Task(id="T", title="x", verify="grep -q old f")  # base でも PASS
            task.extra.append(("verify_source", "synth"))
            self.assertTrue(km.verify_undiscriminating(cfg, task, d, False,
                                                       (base, frozenset()), None))

    def test_redgreen_off_and_human_verify_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = self._git_repo(d, content="old")
            cfg_off = cfg_for(d, workdir=d, verify_validate="off")
            task = km.Task(id="T", title="x", verify="grep -q old f")
            task.extra.append(("verify_source", "synth"))
            self.assertFalse(km.verify_undiscriminating(cfg_off, task, d, False,
                                                        (base, frozenset()), None))
            # synth ポリシーは人が書いた verify（source!=synth/template）を検証しない
            cfg = cfg_for(d, workdir=d)
            human = km.Task(id="T2", title="x", verify="grep -q old f")
            self.assertFalse(km.verify_undiscriminating(cfg, human, d, False,
                                                        (base, frozenset()), None))


class EnsureNeedsTests(unittest.TestCase):
    """needs は status の投影＝失われたら status から作り直す（自己修復）。

    従来は「状態が変わった瞬間」にしか票を書かず、proposed だけが ensure で守られていた。
    そのため blocked/review の票が失われると二度と作られず、backlog は blocked のままなのに
    viewer の要対応画面には出てこない（viewer の操作ボタンは全て needs カードに紐づくため、
    人は承認も再実行も差し戻しもできない袋小路に入った）。"""

    def _cfg(self, d):
        return cfg_for(Path(d), plan_review=True)

    def test_lost_blocked_card_is_rebuilt_with_its_reason(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", status="blocked", verify="true")
            km._remember_needs_reason(t, "繰り返し NG（retries=3）: exit=1")
            km.persist_task(cfg, t)
            self.assertFalse(km.needs_path(cfg, "T1").exists())   # 票が失われた状態

            made = km.ensure_needs(cfg, [t])
            self.assertEqual(made, ["T1"])
            body = km.needs_path(cfg, "T1").read_text(encoding="utf-8")
            self.assertIn("繰り返し NG（retries=3）", body)        # 理由も復元される
            self.assertIn("kind: blocked", body)

    def test_lost_review_card_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T2", title="x", status="review", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            self.assertIn("kind: review", km.needs_path(cfg, "T2").read_text(encoding="utf-8"))

    def test_existing_card_is_never_overwritten(self):
        # 人が記入中の票を消さない
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T3", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            p = km.needs_path(cfg, "T3")
            p.write_text(p.read_text(encoding="utf-8") + "\n人の記入\n", encoding="utf-8")
            self.assertEqual(km.ensure_needs(cfg, [t]), [])       # 再生成しない
            self.assertIn("人の記入", p.read_text(encoding="utf-8"))

    def test_running_states_get_no_card(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            tasks = [km.Task(id=f"T{i}", title="x", status=s, verify="true")
                     for i, s in enumerate(("ready", "doing", "done"))]
            self.assertEqual(km.ensure_needs(cfg, tasks), [])

    def test_enqueue_creates_the_review_card_immediately(self):
        # 従来はループのパス頭まで票が作られず、その間「backlog は承認待ち・要対応画面には無い」
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            args = types.SimpleNamespace(
                json=False, file=None, id="T9", title="新規", verify="true", priority=0,
                source="human", status=None, after=None, review=None, note=None, accept=None,
                verify_template=None, repos=None, cohort_items=None)
            self.assertEqual(km.cmd_enqueue(cfg, args), 0)
            self.assertTrue(km.needs_path(cfg, "T9").exists(), "投入したその場で票ができる")


class DecidedNeedsNotReprojectedTests(unittest.TestCase):
    """人が答え終わった票を作り直さない（総覧 G-2 / コンセプト正典 C3）。

    状態の同期が競合すると backlog は機械状態＝ローカル優先で裁定されるため、人の決定を
    受け取り損ねた PC の status だけが古いまま残りうる。そのまま自己修復（ensure_needs）が
    走ると、答え済みの票が復活して全 PC へ再伝播し、人は同じ判断を二度させられる。
    決定記録（追記のみ＝衝突なく合流する）を根拠にこの再投影だけを止める。"""

    def _cfg(self, d):
        return cfg_for(Path(d), plan_review=True)

    def _decide(self, cfg, tid, to="ready", actor="alice", action="feedback-resume", dr=1):
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        km.decision_path(cfg, tid).write_text(
            f"## DR-{dr:04d}  2026-07-27  actor: {actor}\n"
            f"- context : {tid} に人のフィードバック\n- action  : {action}\n"
            f"- reason  : ok\n- affects : {tid} → {to}\n\n", encoding="utf-8")

    def test_answered_card_is_not_rebuilt_from_a_stale_status(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            self._decide(cfg, "T1", to="ready")            # 人は既に「再開」と決めた
            self.assertEqual(km.ensure_needs(cfg, [t]), [])
            self.assertFalse(km.needs_path(cfg, "T1").exists())
            # 見送りは記録に残す（黙って消えるのが一番困る）。記録は 1 回だけ
            self.assertIn("needs 再投影を見送り: T1", cfg.journal.read_text(encoding="utf-8"))
            before = cfg.journal.read_text(encoding="utf-8")
            self.assertEqual(km.ensure_needs(cfg, km.load_tasks(cfg.backlog)), [])
            self.assertEqual(cfg.journal.read_text(encoding="utf-8"), before)

    def test_machine_decisions_are_not_grounds_for_skipping(self):
        # auto/system/gitlab の記録は「人が答えた」ではない。票は従来どおり作り直す
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T2", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            self._decide(cfg, "T2", to="ready", actor="auto", action="auto-adjudicate")
            self.assertEqual(km.ensure_needs(cfg, [t]), ["T2"])

    def test_human_decision_back_into_judgement_still_projects(self):
        # 差し戻し（→ proposed）のように人の決定そのものが判断待ちなら、投影は止めない
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T3", title="x", status="proposed", verify="true")
            km.persist_task(cfg, t)
            self._decide(cfg, "T3", to="proposed", action="plan-rework")
            self.assertEqual(km.ensure_needs(cfg, [t]), ["T3"])

    def test_blocked_again_after_the_decision_is_a_new_judgement(self):
        # 人の決定の後で機械が改めて止めた（再 blocked）なら、それは新しい判断待ち。
        # 判断待ちへ入れた側が押す印（needs_dr）で作り直しと区別する
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T4", title="x", status="ready", verify="true")
            km.persist_task(cfg, t)
            self._decide(cfg, "T4", to="ready")
            km._block(cfg, t, "また失敗した（retries=3）", {})
            self.assertEqual(t.norm_status(), "blocked")
            km.needs_path(cfg, "T4").unlink()              # 票だけ失われた（同期事故）
            self.assertEqual(km.ensure_needs(cfg, km.load_tasks(cfg.backlog)), ["T4"])

    def test_state_merge_follows_a_deletion_backed_by_a_decision(self):
        # 同時変更の裁定: リモートが票を消し、その削除に決定記録が伴うなら削除に従う。
        # 記録が無ければ従来どおりローカルの票を残す（新しい blocked 票を失わない）。
        decided = {"T1"}.__contains__
        self.assertFalse(km._take_local_on_conflict(
            "needs/T1.md", local_present=True, remote_present=False, decided=decided))
        self.assertTrue(km._take_local_on_conflict(
            "needs/T2.md", local_present=True, remote_present=False, decided=decided))
        # 述語を渡さない呼び出し（既存の契約）は従来の裁定のまま
        self.assertTrue(km._take_local_on_conflict(
            "needs/T1.md", local_present=True, remote_present=False))
        # 本文の同時編集は従来どおり人（リモート）を正とする
        self.assertFalse(km._take_local_on_conflict(
            "needs/T1.md", local_present=True, remote_present=True, decided=decided))


class ReapOrphanNeedsTests(unittest.TestCase):
    """needs は status の投影＝**投影元が消えたら票も消す**（ensure_needs の対）。

    従来は作る側しか無く、タスクを消した後に票だけが残った。残った票は ingest_feedback が
    読み飛ばす（対応タスクが無い）ので [x] を付けても消えず、しかも has_work は「人の入力あり」
    と数える＝人からは「消しても復活する要対応」に見える袋小路だった。"""

    def _cfg(self, d):
        return cfg_for(Path(d), plan_review=True)

    def test_card_without_its_task_is_reaped(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T1", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            self.assertTrue(km.needs_path(cfg, "T1").exists())

            km.delete_task_file(cfg, t)                     # viewer の削除・手作業・同期事故
            self.assertEqual(km.reap_orphan_needs(cfg), ["T1"])
            self.assertFalse(km.needs_path(cfg, "T1").exists())

    def test_card_with_a_live_task_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T2", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            self.assertEqual(km.reap_orphan_needs(cfg), [])
            self.assertTrue(km.needs_path(cfg, "T2").exists())

    def test_milestone_card_is_not_touched(self):
        # milestone 票の持ち主は reconcile_milestones（project.json の status が正）。
        # タスクが無いのは当たり前なので、ここで消すと承認前のマイルストーンが毎パス消える。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            p = cfg.needs / "proj.md"
            p.write_text("---\nstatus: proposed\nkind: milestone\n---\n\n# マイルストーン\n",
                         encoding="utf-8")
            self.assertEqual(km.reap_orphan_needs(cfg), [])
            self.assertTrue(p.exists())

    def test_reconcile_needs_creates_and_reaps_in_one_pass(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            live = km.Task(id="T3", title="x", status="blocked", verify="true")
            km.persist_task(cfg, live)
            gone = km.Task(id="T4", title="y", status="review", verify="true")
            km.persist_task(cfg, gone)
            km.ensure_needs(cfg, [gone])
            km.delete_task_file(cfg, gone)

            made, reaped = km.reconcile_needs(cfg, [live])
            self.assertEqual((made, reaped), (["T3"], ["T4"]))
            self.assertTrue(km.needs_path(cfg, "T3").exists())
            self.assertFalse(km.needs_path(cfg, "T4").exists())

    def test_checked_orphan_stops_waking_the_watch_loop(self):
        # [x] 済みの孤児票は has_work を毎パス真にしていた（起きても何も処理できない空回り）。
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d)
            km.ensure_dirs(cfg)
            t = km.Task(id="T5", title="x", status="blocked", verify="true")
            km.persist_task(cfg, t)
            km.ensure_needs(cfg, [t])
            _submit_feedback(km.needs_path(cfg, "T5"), "やっぱり要らない")
            km.delete_task_file(cfg, t)
            self.assertTrue(km.has_work(cfg))               # 孤児票だけで起きてしまう

            km.reap_orphan_needs(cfg)
            self.assertFalse(km.has_work(cfg))


class TestRejectAndImpact(unittest.TestCase):
    """却下（reject）: 廃止して archive へ退避＋依存先を再審査（proposed）へ＋charter があれば
    再計画を要求。impact: after 逆辺の影響範囲を一覧提示する。"""

    def test_reject_archives_and_reproposes_dependents(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, learn_capture=True)
            mkb(d, "T1", verify="true")
            mkb(d, "T2", verify="true")
            # T2 は T1 に依存・T3 は T2 に依存（推移）
            t2 = km.load_tasks(cfg.backlog)[1]
            t2.extra.append(("after", "T1"))
            km.persist_task(cfg, t2)
            mkb(d, "T3", verify="true")
            t3 = [t for t in km.load_tasks(cfg.backlog) if t.id == "T3"][0]
            t3.extra.append(("after", "T2"))
            km.persist_task(cfg, t3)
            rc = km.cmd_reject(cfg, "T1", "方針転換で不要")
            self.assertEqual(rc, 0)
            # 本体は rejected として archive へ
            self.assertFalse((cfg.backlog / "T1.md").exists())
            arch = (d / "archive" / "T1.md").read_text(encoding="utf-8")
            self.assertIn("rejected", arch)
            self.assertIn("却下記録", arch)
            # 依存先（推移）は proposed に戻り、after から T1 が外れる
            got = {t.id: t for t in km.load_tasks(cfg.backlog)}
            self.assertEqual(got["T2"].norm_status(), "proposed")
            self.assertEqual(got["T3"].norm_status(), "proposed")
            self.assertNotIn("T1", km.task_deps(got["T2"]))
            self.assertTrue((cfg.needs / "T2.md").exists())    # 再審査票
            # avoid（回避知識）が残る
            dec = (cfg.decisions / "T1.md").read_text(encoding="utf-8")
            self.assertIn("- avoid:", dec)
            self.assertIn("reject", dec)

    def test_reject_does_not_request_replan(self):
        # 却下は再計画を要求しない（分解は人の明示操作だけ）。旧仕様は却下のたびに replan を
        # 自動発行して「穴を埋め直して」おり、消したそばから似たタスクが復活する原因だった。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            cfg.charter.write_text("# Charter: demo\n## goal\nx\n## acceptance\n- `true`\n",
                                   encoding="utf-8")
            mkb(d, "T1", verify="true")
            km.cmd_reject(cfg, "T1", "作り直す")
            self.assertFalse(km.replan_request_path(cfg).exists())

    def test_reject_refuses_doing_with_fresh_claim(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", status="doing", verify="true")
            rc = km.cmd_reject(cfg, "T1", "x")
            self.assertEqual(rc, 2)

    def test_rejected_title_not_replanned(self):
        # rejected は archive に居るため _existing_titles に含まれ、同一タイトルの再提案を冪等排除できる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", title="決済APIを追加", verify="true")
            km.cmd_reject(cfg, "T1", "スコープ外")
            self.assertIn("決済APIを追加", km._existing_titles(cfg))

    def test_reject_retires_brief_into_archive(self):
        # 却下でも run ブリーフを退役させる（done の archive_task と同じ）。brief/ に残すと
        # 同じ task-id を再利用したとき前世代の内容が新タスクへ注入される。蓄積は archive の
        # 却下記録へ転記して残す。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", verify="true")
            t = km.load_tasks(cfg.backlog)[0]
            km.append_brief_item(cfg, t, "制約: 外部APIはモックする")
            self.assertTrue((d / "brief" / "T1.md").exists())
            km.cmd_reject(cfg, "T1", "不要")
            self.assertFalse((d / "brief" / "T1.md").exists())
            arch = (cfg.archive_dir() / "T1.md").read_text(encoding="utf-8")
            self.assertIn("run ブリーフ", arch)
            self.assertIn("外部APIはモックする", arch)


class TestDetachAndOrphanGc(unittest.TestCase):
    """物理削除（viewer のファイル操作）との整合: 依存の切り離しと孤児の付随状態の掃除。
    却下（cmd_reject）は自分で切り離すが、削除はエンジンの毎パスの整合点が引き受ける。"""

    def test_prune_dangling_afters_detaches_deleted_predecessor(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "A", verify="true")
            mkb(d, "B", verify="true")
            mkb(d, "C", verify="true")
            tasks = {t.id: t for t in km.load_tasks(cfg.backlog)}
            tasks["B"].extra.append(("after", "A, GHOST"))   # GHOST = 物理削除済み相当
            km.persist_task(cfg, tasks["B"])
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "OLD.md").write_text("## OLD: x\n- status: done\n", encoding="utf-8")
            tasks["C"].extra.append(("after", "OLD"))        # archive の done は切り離さない
            km.persist_task(cfg, tasks["C"])
            pruned = km.prune_dangling_afters(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(pruned, ["B"])
            by = {t.id: t for t in km.load_tasks(cfg.backlog)}
            self.assertEqual(km.task_deps(by["B"]), ["A"])   # 生きている先行は残る
            self.assertEqual(km.task_deps(by["C"]), ["OLD"])  # 実行済みの順序の記録は残る
            # W9: 前提を失った後続は実行可能のまま放置せず、人の再審査（proposed）を通す
            self.assertEqual(by["B"].norm_status(), "proposed")
            self.assertTrue((d / "needs" / "B.md").exists())
            self.assertIn("削除", (d / "needs" / "B.md").read_text(encoding="utf-8"))

    def test_prune_dangling_afters_leaves_doing_running(self):
        """W9: doing は落とさない（実行中の中断はしない。切り離しだけ行う）。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "B", verify="true", status="doing")
            tasks = km.load_tasks(cfg.backlog)
            tasks[0].extra.append(("after", "GHOST"))
            km.persist_task(cfg, tasks[0])
            pruned = km.prune_dangling_afters(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(pruned, ["B"])
            by = {t.id: t for t in km.load_tasks(cfg.backlog)}
            self.assertEqual(km.task_deps(by["B"]), [])
            self.assertEqual(by["B"].norm_status(), "doing")
            self.assertFalse((d / "needs" / "B.md").exists())

    def test_reap_orphan_task_state_removes_stateless_leftovers(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "LIVE", verify="true")
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "DONE.md").write_text("## DONE: x\n- status: done\n", encoding="utf-8")
            for tid in ("LIVE", "DONE", "GONE"):
                (d / "verifications" / tid).mkdir(parents=True, exist_ok=True)
                (d / "verifications" / tid / "r.md").write_text("x", encoding="utf-8")
                (d / "brief").mkdir(exist_ok=True)
                (d / "brief" / f"{tid}.md").write_text("x", encoding="utf-8")
                (d / "claims").mkdir(exist_ok=True)
                (d / "claims" / f"{tid}.lock").write_text("x", encoding="utf-8")
            reaped = km.reap_orphan_task_state(cfg)
            self.assertEqual(reaped, ["DONE", "GONE"])
            # 検証記録・ブリーフ: backlog にも archive にも無い id だけ消す
            self.assertTrue((d / "verifications" / "LIVE").exists())
            self.assertTrue((d / "verifications" / "DONE").exists())
            self.assertFalse((d / "verifications" / "GONE").exists())
            self.assertTrue((d / "brief" / "LIVE.md").exists())
            self.assertTrue((d / "brief" / "DONE.md").exists())
            self.assertFalse((d / "brief" / "GONE.md").exists())
            # claim ロック: backlog に居ない id は実行権の意味を失うので archive 行きでも消す
            self.assertTrue((d / "claims" / "LIVE.lock").exists())
            self.assertFalse((d / "claims" / "DONE.lock").exists())
            self.assertFalse((d / "claims" / "GONE.lock").exists())

    def test_run_pass_detaches_and_reaps(self):
        # 呼び出し点の確認: 通常の run パス（_run_setup の整合点）で切り離しと掃除が走る。
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "B", verify="true")
            tasks = km.load_tasks(cfg.backlog)
            tasks[0].extra.append(("after", "GHOST"))
            km.persist_task(cfg, tasks[0])
            (d / "brief").mkdir(exist_ok=True)
            (d / "brief" / "GHOST.md").write_text("x", encoding="utf-8")
            km.run_loop(cfg, act=lambda t, c, loc: (True, "ok"))
            self.assertFalse((d / "brief" / "GHOST.md").exists())
            journal = cfg.journal.read_text(encoding="utf-8")
            self.assertIn("依存の切り離し: B", journal)

    def test_impact_lists_upstream_and_downstream(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "A", verify="true")
            mkb(d, "B", verify="true")
            mkb(d, "C", verify="true")
            tasks = {t.id: t for t in km.load_tasks(cfg.backlog)}
            tasks["B"].extra.append(("after", "A"))
            km.persist_task(cfg, tasks["B"])
            tasks["C"].extra.append(("after", "B"))
            km.persist_task(cfg, tasks["C"])
            all_tasks = km.load_tasks(cfg.backlog)
            downs = [t.id for t in km.dependents_of(all_tasks, "A")]
            self.assertEqual(sorted(downs), ["B", "C"])        # 推移閉包
            ups = km.prerequisites_of(all_tasks, "C")
            self.assertEqual(sorted(ups), ["A", "B"])
            self.assertEqual(km.cmd_impact(cfg, "A"), 0)
            self.assertEqual(km.cmd_impact(cfg, "zzz"), 2)
