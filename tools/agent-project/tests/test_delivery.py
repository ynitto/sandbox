"""agent-project の単体テスト — delivery（`test_agent_project.py` から機能別に分割）。

共有の前置き（環境隔離・`km` のロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-project/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class TestDelivery(unittest.TestCase):
    def test_extract_ref(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            self.assertIn("/pull/42", km.extract_delivery_ref("done https://github.com/o/r/pull/42 ok", cfg))
            self.assertIn("commit", km.extract_delivery_ref("created abcdef1 done", cfg))

    def test_delivery_note_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", title="納品物A", verify="true")

            def fake_act(task, cfg, location="local"):
                return True, "pushed https://github.com/o/r/pull/7"

            res = km.run_loop(cfg_for(d, dry_run=False), act=fake_act)
            self.assertEqual(res["counts"]["done"], 1)
            note = (d / "archive" / "T1.md").read_text()
            self.assertIn("## 納品書", note)
            self.assertIn("/pull/7", note)
            manifest = (d / "DELIVERY.md").read_text()
            self.assertIn("納品物A", manifest)
            self.assertIn("/pull/7", manifest)


class TestRunBrief(unittest.TestCase):
    """run ブリーフ（run/branch スコープ・差し戻し意図とノード発見制約の蓄積・伝播）。"""

    def _cfg(self, d, **kw):
        cfg = cfg_for(d, **kw)
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        cfg.journal.parent.mkdir(parents=True, exist_ok=True)
        return cfg

    def _task(self, tid="T1"):
        return km.Task(id=tid, title="やる", verify="true")

    def test_append_normalizes_dedupes_and_is_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            self.assertTrue(km.append_brief_item(cfg, t, "  - API は snake_case で統一\n  する ",
                                                 source="feedback"))
            # 正規化（改行畳み・箇条書き記号除去）後は同一 → 重複は追記しない（冪等）
            self.assertFalse(km.append_brief_item(cfg, t, "API は snake_case で統一 ⏎ する",
                                                  source="revise"))
            # 既出は弾かれ、新規だけ追記される（回収経路は capture_insight が 1 件ずつ呼ぶ）
            self.assertFalse(km.capture_insight(cfg, t, "API は snake_case で統一 ⏎ する", source="node"))
            self.assertTrue(km.capture_insight(cfg, t, "配置は src/ 配下", source="node"))
            self.assertEqual(km._brief_items(cfg, t),
                             ["API は snake_case で統一 ⏎ する", "配置は src/ 配下"])

    def test_brief_lives_at_root_beside_rules(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            km.append_brief_item(cfg, t, "x", source="feedback")
            bp = km.brief_path(cfg, t)
            # run ブリーフは rules.md と同じ <root> 直下（backlog の親）の brief/ に置かれる
            self.assertEqual(bp.parent, km.rules_path(cfg).parent / "brief")
            self.assertEqual(bp.name, "T1.md")

    def test_empty_and_missing_are_safe(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            self.assertFalse(km.append_brief_item(cfg, t, "   ", source="feedback"))
            self.assertFalse(km.brief_path(cfg, t).exists())     # 空はブリーフを作らない
            self.assertEqual(km.brief_context(cfg, t), "")       # 無いブリーフは空注入

    def test_build_request_injects_brief_and_discovery_instruction(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            # ブリーフが空なら制約ブロックは出ない（後方互換）
            self.assertNotIn("この run のブリーフ", km.build_request(t, cfg))
            km.append_brief_item(cfg, t, "エラーメッセージは英語で統一", source="feedback")
            req = km.build_request(t, cfg)
            self.assertIn("この run のブリーフ", req)
            self.assertIn("エラーメッセージは英語で統一", req)
            # task_branch 有効時はノード発見制約の提示契約も載る
            self.assertIn('{"constraints"', req)

    def test_discovery_instruction_gated_on_task_branch(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d), task_branch=False)
            self.assertNotIn('{"constraints"', km.build_request(self._task(), cfg))

    def test_feedback_ingest_accumulates_into_brief(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            cfg.backlog.mkdir(parents=True, exist_ok=True)
            (cfg.backlog / "T1.md").write_text(
                "## T1: y\n- status: ready\n- verify: `true`\n- review: human\n- retries: 0\n",
                encoding="utf-8")
            km.run_loop(cfg)                                  # verify PASS → review 待ち（needs 生成）
            nf = cfg.needs / "T1.md"
            # 1 回目の差し戻し
            nf.write_text(nf.read_text(encoding="utf-8").replace("- [ ] 確定", "- [x] 確定")
                          + "\n## フィードバック\n命名は snake_case で統一\n", encoding="utf-8")
            km.ingest_feedback(cfg, km.load_tasks(cfg.backlog))
            # 2 回目: 再度 review に載せ、別の差し戻し（feedback フィールドは上書きされる）
            t = km.load_tasks(cfg.backlog)[0]
            t.status = "review"
            km.persist_task(cfg, t)
            nf.write_text("## Decision Outcome\n- [x] 確定\n## フィードバック\nログは英語で統一\n",
                          encoding="utf-8")
            km.ingest_feedback(cfg, km.load_tasks(cfg.backlog))
            # ブリーフは追記のみ＝最初の差し戻し（snake_case）が消えず、両方が残る
            items = km._brief_items(cfg, km.Task(id="T1", title="y"))
            self.assertTrue(any("snake_case" in x for x in items))
            self.assertTrue(any("ログは英語" in x for x in items))

    def test_revise_accumulates_into_brief(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            cfg.backlog.mkdir(parents=True, exist_ok=True)
            km.persist_task(cfg, km.Task(id="T1", title="やる", verify="true", status="ready"))
            km.cmd_revise(cfg, "T1", {}, feedback="配置は src/ 配下に統一", reason="revise")
            items = km._brief_items(cfg, km.Task(id="T1", title="やる"))
            self.assertTrue(any("src/ 配下" in x for x in items))

    def test_read_brief_discoveries_parses_result_json(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(Path(d))
            payload = {"final_nodes": [
                {"id": "n1-reduce", "kind": "reduce",
                 "data": {"count": 3, "constraints": ["日付は ISO8601", {"text": "単位は SI"}, ""]}},
                {"id": "n2", "kind": "work", "data": None},
            ]}

            class _P:
                returncode = 0
                stdout = json.dumps(payload)

            with mock.patch.object(km.subprocess, "run", return_value=_P()):
                got = km.read_brief_discoveries(cfg, False, run_id="req-x")
            self.assertEqual(got, ["日付は ISO8601", "単位は SI"])   # dict/str 両対応・空は除外


class TestCaptureInsightAndRetireBrief(unittest.TestCase):
    """capture_insight（捕捉の単一入口: brief＋learn の 2 スコープ射影）と
    retire_brief（完了時のブリーフ退役: 納品書へ転記して掃除）。"""

    def _cfg(self, d, **kw):
        cfg = cfg_for(d, **kw)
        cfg.decisions.mkdir(parents=True, exist_ok=True)
        cfg.journal.parent.mkdir(parents=True, exist_ok=True)
        return cfg

    def _task(self, tid="T1"):
        return km.Task(id=tid, title="API を作る", verify="true")

    def test_capture_projects_to_brief_and_learn(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            self.assertTrue(km.capture_insight(cfg, t, "API は snake_case で統一",
                                               source="node", learn=True))
            # task スコープ: run ブリーフに載る（同一タスクの次 run へ即時伝播）
            self.assertIn("snake_case", km.brief_context(cfg, t))
            # project スコープ: learn 行が decisions/ に残り、rules 昇格ラダーに乗る
            learns = [(src, guide) for src, _t, guide in km.collect_learnings(cfg)]
            self.assertTrue(any("snake_case" in g for _s, g in learns))

    def test_capture_without_learn_is_brief_only(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            self.assertTrue(km.capture_insight(cfg, t, "cohort 波及の指摘", source="cohort"))
            self.assertIn("cohort 波及の指摘", km.brief_context(cfg, t))
            self.assertEqual(km.collect_learnings(cfg), [])   # learn 射影しない（発生源で捕捉済み）

    def test_capture_dedup_does_not_duplicate_learn(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            self.assertTrue(km.capture_insight(cfg, t, "重複させない", source="node", learn=True))
            self.assertFalse(km.capture_insight(cfg, t, "重複させない", source="node", learn=True))
            self.assertEqual(len(km.collect_learnings(cfg)), 1)   # 冪等: learn も 1 件のまま

    def test_capture_respects_learn_capture_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d), learn_capture=False), self._task()
            self.assertTrue(km.capture_insight(cfg, t, "ルール", source="node", learn=True))
            self.assertEqual(km.collect_learnings(cfg), [])   # learn 捕捉は設定で無効化できる

    def test_retire_brief_returns_items_and_deletes_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, t = self._cfg(Path(d)), self._task()
            km.append_brief_item(cfg, t, "制約A", source="feedback")
            km.append_brief_item(cfg, t, "制約B", source="node")
            body = km.retire_brief(cfg, t)
            self.assertIn("制約A", body)
            self.assertIn("制約B", body)
            self.assertFalse(km.brief_path(cfg, t).exists())   # 退役＝ファイルは消える
            self.assertEqual(km.retire_brief(cfg, t), "")      # 冪等（2 回目は空）

    def test_archive_embeds_brief_and_prevents_stale_reinjection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, t = self._cfg(d), self._task()
            cfg.backlog.mkdir(parents=True, exist_ok=True)
            km.persist_task(cfg, t)
            km.append_brief_item(cfg, t, "エラーメッセージは英語で統一", source="feedback")
            km.archive_task(cfg, t, "PASS", "ref", "2026-07-16")
            adoc = (cfg.archive_dir() / "T1.md").read_text(encoding="utf-8")
            self.assertIn("run ブリーフ", adoc)                # 納品書に成果物として転記される
            self.assertIn("エラーメッセージは英語で統一", adoc)
            self.assertFalse(km.brief_path(cfg, t).exists())
            # 同じ task-id を再利用しても前世代のブリーフが注入されない
            self.assertEqual(km.brief_context(cfg, self._task("T1")), "")

    def test_archive_without_brief_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg, t = self._cfg(d), self._task()
            cfg.backlog.mkdir(parents=True, exist_ok=True)
            km.persist_task(cfg, t)
            km.archive_task(cfg, t, "PASS", "ref", "2026-07-16")
            adoc = (cfg.archive_dir() / "T1.md").read_text(encoding="utf-8")
            self.assertNotIn("run ブリーフ", adoc)             # ブリーフ無しなら節も出ない（後方互換）

    def test_delivery_can_be_rebuilt_from_archive_union(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = self._cfg(d)
            for tid in ("T1", "T2"):
                task = self._task(tid)
                km.persist_task(cfg, task)
                km.archive_task(cfg, task, "PASS", f"ref-{tid}", "2026-07-16")
            cfg.delivery.write_text("lost row\n", encoding="utf-8")
            km.rebuild_delivery(cfg)
            delivery = cfg.delivery.read_text(encoding="utf-8")
            self.assertIn("| T1 |", delivery)
            self.assertIn("| T2 |", delivery)


class TestTaskBranchAndDeliveryReview(unittest.TestCase):
    """タスク単位ターゲットブランチ（ap/<task-id>）と成果物レビュー（delivery_review・本番既定 on）。
    review 到達時に MR を用意し、承認で Stage 2 と同一規則の自動決着（クリーンならマージ）を行う。"""

    def test_workspace_spec_injects_task_branch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True, task_branch_prefix="ap/")
            mkb(d, "T1")
            t = km.load_tasks(cfg.backlog)[0]
            t.extra.append(("workspace", "https://gitlab.example.com/g/app.git"))
            spec = km._workspace_spec_for(cfg, t)
            self.assertEqual(spec["branch"], "ap/T1")          # 全試行を集約するブランチ
            token = km._workspace_token(spec)
            self.assertIn('"branch":"ap/T1"', token)           # agent-flow へ伝搬
            cfg2 = cfg_for(d, task_branch=False)
            spec2 = km._workspace_spec_for(cfg2, t)
            self.assertNotIn("branch", spec2 or {})            # off なら従来（run 毎 af/<run-id>）

    def test_delivery_review_gates_done(self):
        # delivery_review=True: verify PASS でも自動 done せず review（検収待ち）へ
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, delivery_review=True, learn=False, auto_adjudicate=False)
            mkb(d, "T1", verify="true")
            result = km.run_loop(cfg)
            self.assertEqual(result["counts"]["review"], 1)    # done でなく検収待ち
            self.assertEqual(result["counts"]["done"], 0)
            t = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(t.norm_status(), "review")
            self.assertTrue((cfg.needs / "T1.md").exists())
            self.assertEqual(km.exit_code_for(result), 1)      # 人の対応待ち

    def test_gl_parse_repo_forms(self):
        self.assertEqual(km._gl_parse_repo("https://gitlab.com/g/app.git"),
                         ("gitlab.com", "g/app"))
        self.assertEqual(km._gl_parse_repo("https://gl.example.com/team/sub/app"),
                         ("gl.example.com", "team/sub/app"))
        self.assertEqual(km._gl_parse_repo("git@gitlab.com:g/app.git"),
                         ("gitlab.com", "g/app"))
        self.assertIsNone(km._gl_parse_repo("/local/path/repo"))

    def _mr_task(self, cfg, d):
        mkb(d, "T1", verify="true")
        t = km.load_tasks(cfg.backlog)[0]
        t.extra.append(("workspace", "https://gitlab.example.com/g/app.git"))
        return t

    def test_ensure_task_mr_creates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data, params))
                if method == "GET" and path.endswith("/merge_requests"):
                    return []                                   # 既存 MR 無し
                if method == "POST" and path.endswith("/merge_requests"):
                    return {"iid": 7, "web_url": "https://gitlab.example.com/g/app/-/merge_requests/7"}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                url = km.ensure_task_mr(cfg, t)
            self.assertIn("/merge_requests/7", url)
            self.assertEqual(t.get("mr_iid"), "7")
            post = next(c for c in calls if c[0] == "POST")
            self.assertEqual(post[2]["source_branch"], "ap/T1")
            self.assertTrue(post[2]["remove_source_branch"])
            # 冪等: mr_url 記録済みなら API を呼ばない
            with mock.patch.object(km, "_gl_api", side_effect=AssertionError("再作成しない")):
                self.assertEqual(km.ensure_task_mr(cfg, t), url)

    def test_ensure_task_mr_skips_without_gitlab(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            with mock.patch.object(km, "_gl_token", return_value=""):
                self.assertEqual(km.ensure_task_mr(cfg, t), "")   # トークン無し＝記録のみで続行

    def test_delivery_entries_compares_task_branch_with_target(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            meta = {
                "workspace": {
                    "url": "https://gitlab.example.com/g/app.git",
                    "base": "main",
                    "target": "release",
                    "branch": "ap/T1",
                }
            }
            with mock.patch.object(km, "_task_run_meta", return_value=meta), \
                 mock.patch.object(km, "work_branch_changes", return_value=("origin/ap/T1", ["src/a.py"])) as changes:
                entries = km.delivery_entries(cfg, t)
            changes.assert_called_once_with(cfg, "release", "ap/T1", repo=mock.ANY)
            self.assertEqual(entries[0]["target"], "release")
            self.assertEqual(entries[0]["base"], "release")

    def test_approve_without_mr_fast_forwards_target_branch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            t.status = "review"
            km.persist_task(cfg, t)
            calls = []

            def run(cmd, **kwargs):
                calls.append(cmd)
                if "merge-base" in cmd and cmd[-2:] == ["origin/ap/T1", "origin/release"]:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with mock.patch.object(km, "ensure_task_mr", return_value=""), \
                 mock.patch.object(km, "_task_work_branch", return_value=("release", "ap/T1")), \
                 mock.patch.object(km, "work_branch_changes", return_value=("origin/ap/T1", ["src/a.py"])), \
                 mock.patch.object(km.subprocess, "run", side_effect=run):
                ok, msg = km.finalize_task_delivery(cfg, t)
            self.assertTrue(ok, msg)
            self.assertTrue(any(cmd[-2:] == ["origin/ap/T1:refs/heads/release", "--porcelain"]
                                for cmd in calls if "push" in cmd))

    def test_approve_without_mr_cleanly_merges_diverged_target(self):
        """target が進んでも競合が無ければ、検収承認で安全に統合する。"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            calls = []

            def run(cmd, **kwargs):
                calls.append(cmd)
                # 作業→target も target→作業も ancestor でない（分岐）。
                if "merge-base" in cmd:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
                if "rev-parse" in cmd and cmd[-1] == "HEAD":
                    return subprocess.CompletedProcess(cmd, 0, stdout="merge-commit\n", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with mock.patch.object(km, "ensure_task_mr", return_value=""), \
                 mock.patch.object(km, "_task_work_branch", return_value=("release", "ap/T1")), \
                 mock.patch.object(km, "work_branch_changes",
                                   return_value=("origin/ap/T1", ["src/a.py"])), \
                 mock.patch.object(km.subprocess, "run", side_effect=run):
                ok, msg = km.finalize_task_delivery(cfg, t)

            self.assertTrue(ok, msg)
            self.assertTrue(any("worktree" in cmd and "add" in cmd for cmd in calls))
            self.assertTrue(any(cmd[-2:] == ["merge-commit:refs/heads/release", "--porcelain"]
                                for cmd in calls if "push" in cmd))

    def _review_task_with_mr(self, cfg, d):
        t = self._mr_task(cfg, d)
        t.status = "review"
        t.extra += [("mr_url", "u7"), ("mr_iid", "7"), ("mr_project", "gitlab.example.com|g/app")]
        km.persist_task(cfg, t)
        return t

    # --- フォージ側シグナルからの決着（S4-3・S4-4） -------------------------------------

    def _poll(self, cfg, t, mr, *, reviewers=None, discussions=None, remote_review="settle"):
        """poll_task_mrs を GitLab API モックで 1 回まわす。"""
        cfg.remote_review = remote_review

        def api(host, token, method, path, data=None, params=None):
            if path.endswith("/merge_requests/7"):
                return mr
            if path.endswith("/reviewers"):
                return reviewers or []
            if path.endswith("/discussions"):
                return discussions or []
            if path.endswith("/changes"):
                return {"changes": [{"new_path": "a.py"}]}
            return {}

        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "_gl_api", side_effect=api):
            return km.poll_task_mrs(cfg, [t])

    def test_merged_mr_settles_as_approve(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            self.assertEqual(self._poll(cfg, t, {"state": "merged"}), [t.id])
            self.assertEqual(km._load_task_file(cfg, t.id), None)   # done＝backlog から消える

    def test_closed_unmerged_mr_settles_as_reject(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            self.assertEqual(self._poll(cfg, t, {"state": "closed"}), [t.id])
            # reject は backlog から外し archive へ退避する（cmd_reject と同じ経路）
            self.assertIsNone(km._load_task_file(cfg, t.id))
            self.assertIn("reject", cfg.journal.read_text(encoding="utf-8"))

    def test_changes_requested_label_settles_as_revise_with_unresolved_notes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            discussions = [{"notes": [
                {"body": "ここは null チェックが要ります", "resolvable": True, "resolved": False},
                {"body": "解決済みの指摘", "resolvable": True, "resolved": True},
                {"body": "システム通知", "system": True, "resolvable": False},
                {"body": "agent-project: 自動コメント", "resolvable": True, "resolved": False},
            ]}]
            before = t.retries
            got = self._poll(cfg, t, {"state": "opened", "labels": ["status:changes-requested"]},
                             discussions=discussions)
            self.assertEqual(got, [t.id])
            fresh = km._load_task_file(cfg, t.id)
            fb = fresh.get("feedback")
            self.assertIn("null チェック", fb)
            # 解決済み・system・自分のコメントは注入しない（毎回積み直して収束しなくなる）
            self.assertNotIn("解決済みの指摘", fb)
            self.assertNotIn("システム通知", fb)
            self.assertNotIn("自動コメント", fb)
            self.assertEqual(fresh.norm_status(), "ready")
            self.assertEqual(fresh.retries, before + 1, "差し戻しは新しい run（同 run 再開にしない）")

    def test_changes_requested_review_state_also_settles_as_revise(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            got = self._poll(cfg, t, {"state": "opened"},
                             reviewers=[{"state": "requested_changes"}],
                             discussions=[{"notes": [{"body": "直して", "resolvable": True,
                                                      "resolved": False}]}])
            self.assertEqual(got, [t.id])

    def test_comments_only_do_not_settle(self):
        # キーワード推定は使わない: 「やり直し」と書かれていてもラベル/レビュー状態が無ければ
        # 決着しない（書き手の言い回し 1 つで判定が変わる状態をやめる、が S4-3 の趣旨）
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            got = self._poll(cfg, t, {"state": "opened", "labels": ["discussion"]},
                             discussions=[{"notes": [{"body": "やり直しかも？", "resolvable": True,
                                                      "resolved": False}]}])
            self.assertEqual(got, [])
            self.assertEqual(km._load_task_file(cfg, t.id).norm_status(), "review")

    def test_forge_unreachable_does_not_settle(self):
        # 到達不能を「未マージ＝却下」と読むと、回線が切れただけで成果が却下される
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=RuntimeError("接続できません")):
                self.assertEqual(km.poll_task_mrs(cfg, [t]), [])
            self.assertEqual(km._load_task_file(cfg, t.id).norm_status(), "review")

    def test_observe_mode_reports_but_does_not_settle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            self.assertEqual(self._poll(cfg, t, {"state": "merged"}, remote_review="observe"), [])
            self.assertEqual(km._load_task_file(cfg, t.id).norm_status(), "review")
            self.assertIn("remote_review(observe)", cfg.journal.read_text(encoding="utf-8"))

    def test_poll_skips_tasks_without_mr_or_not_in_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            t.status = "ready"
            with mock.patch.object(km, "_gl_api", side_effect=AssertionError("照会しない")):
                self.assertEqual(km.poll_task_mrs(cfg, [t]), [])
            plain = self._mr_task(cfg, d)
            plain.status = "review"
            with mock.patch.object(km, "_gl_api", side_effect=AssertionError("照会しない")):
                self.assertEqual(km.poll_task_mrs(cfg, [plain]), [])

    # --- フォージ境界（S4-1.7） ---------------------------------------------------------

    def test_forge_available_only_gitlab(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            with mock.patch.object(km, "_gl_token", return_value="tok"):
                self.assertEqual(km.forge_available(cfg, "https://gitlab.example.com/g/a.git"),
                                 "gitlab")
                # 自己ホスト GitLab（ホスト名で判別できない）はトークンの有無で決める
                self.assertEqual(km.forge_available(cfg, "https://git.example.com/g/a.git"),
                                 "gitlab")
                # GitHub / Gitea は境界だけ切って未対応 → フォージ無し運用へ倒す
                self.assertEqual(km.forge_available(cfg, "https://github.com/o/r.git"), "")
                self.assertEqual(km.forge_available(cfg, "https://gitea.example.com/o/r.git"), "")
            with mock.patch.object(km, "_gl_token", return_value=""):
                self.assertEqual(km.forge_available(cfg, "https://git.example.com/g/a.git"), "")

    def test_ensure_task_mr_skips_unsupported_forge(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, task_branch=True)
            t = self._mr_task(cfg, d)
            t.drop("workspace")
            t.extra.append(("workspace", "https://github.com/o/r.git"))
            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=AssertionError("叩かない")):
                self.assertEqual(km.ensure_task_mr(cfg, t), "")

    def test_approve_merges_clean_mr_and_finalizes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                if method == "GET" and path.endswith("/merge_requests/7"):
                    return {"state": "opened", "merge_status": "can_be_merged",
                            "has_conflicts": False}
                if path.endswith("/discussions"):
                    return []
                if path.endswith("/changes"):
                    return {"changes": [{"new_path": "a.py"}]}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_approve(cfg, t.id, "検収OK")
            self.assertEqual(rc, 0)
            self.assertTrue(any(m == "PUT" and p.endswith("/merge") for m, p, _ in calls))
            self.assertTrue((d / "archive" / f"{t.id}.md").exists())   # done 確定

    def test_approve_unclean_mr_keeps_review(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                if method == "GET" and path.endswith("/merge_requests/7"):
                    return {"state": "opened", "merge_status": "cannot_be_merged",
                            "has_conflicts": True}
                if path.endswith("/discussions"):
                    return []
                if path.endswith("/changes"):
                    return {"changes": [{"new_path": "a.py"}]}
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_approve(cfg, t.id, "検収OK")
            self.assertEqual(rc, 1)                              # done にしない
            got = km.load_tasks(cfg.backlog)[0]
            self.assertEqual(got.norm_status(), "review")        # review のまま
            note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
            self.assertIn("差し戻し", note[2]["body"])

    def test_approve_without_mr_is_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            mkb(d, "T1", status="review", verify="true")
            rc = km.cmd_approve(cfg, "T1", "OK")
            self.assertEqual(rc, 0)                              # MR 無しは従来どおり done 確定のみ
            self.assertTrue((d / "archive" / "T1.md").exists())

    def test_reject_closes_mr_and_deletes_branch(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d)
            t = self._review_task_with_mr(cfg, d)
            calls = []

            def api(host, token, method, path, data=None, params=None):
                calls.append((method, path, data))
                return {}

            with mock.patch.object(km, "_gl_token", return_value="tok"), \
                 mock.patch.object(km, "_gl_api", side_effect=api):
                rc = km.cmd_reject(cfg, t.id, "作り直す")
            self.assertEqual(rc, 0)
            self.assertTrue(any(m == "PUT" and p.endswith("/merge_requests/7")
                                and (dd or {}).get("state_event") == "close"
                                for m, p, dd in calls))          # MR クローズ
            self.assertTrue(any(m == "DELETE" and "/repository/branches/" in p
                                for m, p, _ in calls))           # ソースブランチ削除


class DeliveryEvidenceTests(unittest.TestCase):
    """検収の判断材料は「レビューすべき実体のコード」を示すこと。

    成果物は worker が作業ブランチ ap/<task-id> へコミットする。ところが判断材料は cfg.workdir を
    見ていて、状態 worktree（<repo>-agent-state/.agent-project）を指すため、出てくるのは bus/ の
    claims や events の JSON ばかりだった。人は「何をどうレビューすればいいのか分からない」まま
    承認を迫られる（実際そうなっていた: 変更ファイル 23 件がすべて bus の内部ファイル）。"""

    def _repo(self):
        top = Path(tempfile.mkdtemp(prefix="kp-ev-")).resolve()
        self.addCleanup(shutil.rmtree, top, True)
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false"}
        run = lambda *a: subprocess.run(a, cwd=top, capture_output=True, env=env)
        run("git", "init", "-b", "main", ".")
        run("git", "config", "user.email", "t@e.com")
        run("git", "config", "user.name", "t")
        (top / "src.py").write_text("x = 1\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "init")
        # worker の作業ブランチ（実体のコード変更）
        run("git", "checkout", "-q", "-b", "ap/T1")
        (top / "src.py").write_text("x = 2\n")
        (top / "test_src.py").write_text("assert True\n")
        run("git", "add", "-A")
        run("git", "commit", "-m", "[agent-flow] t1")
        run("git", "checkout", "-q", "main")
        return top

    _URL = "https://gitlab.example.com/g/app.git"

    def _cfg_with_run(self, top, d):
        """本番と同じ形（状態ルートと成果物リポジトリは別物）で cfg と run メタを組む。

        S1 で状態ルートは状態専用リポジトリの clone になり、「リダイレクト前の root＝成果物
        リポジトリ」という暗黙のアンカー（旧 `cfg.state_top`）は無くなった。成果物の所在は
        **タスクの書込先 URL → host.yaml `repos[].local`** で解決する（`_source_repo`）ので、
        テストもその宣言を置く。"""
        use_host_config(self, {"repos": [{"url": self._URL, "local": str(top)}]},
                        home=Path(d) / "agents-home")
        cfg = cfg_for(d)
        t = km.Task(id="T1", title="直す", status="doing", verify="true")
        t.extra.append(("last_run", "req-abc-T1-r0"))
        p = cfg.bus / "runs" / "req-abc-T1-r0"
        p.mkdir(parents=True, exist_ok=True)
        (p / "meta.json").write_text(json.dumps({
            "status": "done",
            "workspace": {"url": self._URL, "base": "main", "branch": "ap/T1"}}),
            encoding="utf-8")
        return cfg, t

    def test_evidence_lists_the_real_code_files(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            ev = km.delivery_evidence(cfg, "", None, "local",
                                      verify="true", vmsg="ok", ok=True, task=t)
            self.assertIn("ap/T1", ev, "成果物は作業ブランチ")
            self.assertIn("src.py", ev, "レビューすべき実体ファイルが出る")
            self.assertIn("test_src.py", ev)
            self.assertIn("diff main...", ev, "差分を開くコマンドを添える")
            self.assertNotIn("bus/", ev, "bus の内部ファイルは判断材料ではない")
            self.assertNotIn("claims", ev)

    def test_risk_counts_real_files_not_bus_internals(self):
        # 大差分（>=10）判定も実体ファイルで行う。bus の JSON を数えると無関係に med へ跳ねる。
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            wb = km._task_work_branch(cfg, t)
            self.assertEqual(wb, ("main", "ap/T1"))
            _ref, files = km.work_branch_changes(cfg, *wb, task=t)
            self.assertEqual(sorted(files), ["src.py", "test_src.py"])

    def test_work_branch_is_recovered_after_run_metadata_is_cleaned(self):
        """検収時までに bus run が GC 済みでも、永続化した workspace から作業 branch を復元する。"""
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg = cfg_for(Path(d), task_branch=True, task_branch_prefix="ap/")
            t = km.Task(id="T1", title="直す", status="blocked", verify="true")
            # workspace がローカルパスそのものの形（`_raw_url_spec`）。この場合は宣言も
            # ミラーも要らず、そのディレクトリが成果物リポジトリとして解決される。
            t.extra.append(("workspace", str(top)))
            self.assertEqual(km._task_work_branch(cfg, t), ("main", "ap/T1"))
            entries = km.delivery_entries(cfg, t)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["branch"], "ap/T1")
            self.assertEqual(sorted(entries[0]["files"]), ["src.py", "test_src.py"])

    def test_falls_back_when_no_work_branch(self):
        # 単発実行（作業ブランチが無い）では従来どおり workdir を見る
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            t = km.Task(id="T9", title="単発", verify="true")
            ev = km.delivery_evidence(cfg, "", None, "local", task=t)
            self.assertIn("- 成果物:", ev)

    def test_delivery_entries_include_references_and_mr(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            # 参照リポジトリを run メタへ追記（agent-flow が書く形）
            meta_path = cfg.bus / "runs" / "req-abc-T1-r0" / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["references"] = [{"url": "https://gitlab.example.com/g/spec.git", "branch": "main"}]
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            mr = "https://gitlab.example.com/g/app/-/merge_requests/42"
            entries = km.delivery_entries(cfg, t, mr_url=mr)
            self.assertGreaterEqual(len(entries), 2)
            self.assertEqual(entries[0]["role"], "write")
            self.assertEqual(entries[0]["mr_url"], mr)
            self.assertIn("src.py", entries[0]["files"])
            self.assertEqual(entries[1]["role"], "reference")
            self.assertIn("spec", entries[1]["name"])
            ev = km.delivery_evidence(cfg, "", None, "local",
                                      verify="true", vmsg="ok", ok=True, task=t, mr_url=mr)
            self.assertIn("リポジトリ:", ev)
            self.assertIn("参照（読取）", ev)
            self.assertIn(mr, ev)

    def test_write_needs_embeds_delivery_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            mr = "https://gitlab.example.com/g/app/-/merge_requests/7"
            delivery = km.delivery_entries(cfg, t, mr_url=mr)
            km.write_needs_file(cfg, t, "検収待ち", review=True,
                                evidence="- 変更ファイル（1 件）:\n    - src.py\n",
                                mr_url=mr, delivery=delivery)
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn(f"mr-url: {mr}", text)
            self.assertIn("delivery: [", text)
            self.assertIn('"role":"write"', text)

    def test_blocked_needs_embeds_delivery_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            delivery = km.delivery_entries(cfg, t)
            km.write_needs_file(cfg, t, "回帰検知", evidence="- 検証: `false` → FAIL",
                                delivery=delivery)
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("kind: blocked", text)
            self.assertIn("delivery: [", text)
            self.assertIn('"path":"' + str(top), text)

    def test_block_transition_attaches_run_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            reasons = {}
            km._block(cfg, t, "回帰検知", reasons, evidence="- 検証: `false` → FAIL")
            text = (cfg.needs / "T1.md").read_text(encoding="utf-8")
            self.assertIn("delivery: [", text)
            self.assertIn('"path":"' + str(top), text)



    def test_unresolved_work_branch_ref_does_not_fall_back_to_workdir(self):
        # meta にブランチ名はあるがローカルで ref が取れないとき、bus 差分へ落とさない。
        with tempfile.TemporaryDirectory() as d:
            top = self._repo()
            cfg, t = self._cfg_with_run(top, Path(d))
            meta_path = cfg.bus / "runs" / "req-abc-T1-r0" / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["workspace"]["branch"] = "ap/DOES-NOT-EXIST"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            # 状態 worktree 側に bus ファイルを書いて、フォールバックするとそれに引っかかるようにする
            (cfg.workdir / "bus").mkdir(parents=True, exist_ok=True)
            (cfg.workdir / "bus" / "junk.json").write_text("{}", encoding="utf-8")
            mr = "https://gitlab.example.com/g/app/-/merge_requests/99"
            entries = km.delivery_entries(cfg, t, mr_url=mr)
            self.assertEqual(entries[0]["role"], "write")
            self.assertEqual(entries[0]["branch"], "ap/DOES-NOT-EXIST")
            self.assertEqual(entries[0]["ref"], "")
            self.assertEqual(entries[0]["mr_url"], mr)
            ev = km.delivery_evidence(cfg, "", "HEAD", "local",
                                      verify="true", vmsg="ok", ok=True, task=t, mr_url=mr)
            self.assertIn("ref 未解決", ev)
            self.assertIn("ap/DOES-NOT-EXIST", ev)
            self.assertIn(mr, ev)
            self.assertNotIn("bus/", ev)
            self.assertNotIn("junk.json", ev)
            self.assertNotIn("- 差分:", ev, "workdir の差分リストへ落とさない")


class TestTaskGuideFields(unittest.TestCase):
    """誘導・レビュー記述フィールド（why/desc/scope/out_of_scope/constraints/hints/demo）。
    人のレビュー材料（needs 票）とワーカー誘導（act 要求文）の両方へ届くことを検証する。"""

    def test_task_from_spec_carries_guide_fields(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            t = km.task_from_spec(cfg, {
                "title": "x", "verify": "true",
                "why": "初見の人が目的を掴めるように",
                "scope": "README.md のみ",
                "out_of_scope": ["目次の追加", "既存節の書き換え"],   # リストは ⏎ 連結で 1 行化
                "constraints": "見出し階層は h2\nを維持",             # 生の改行も ⏎ に畳む
            })
            self.assertEqual(t.get("why"), "初見の人が目的を掴めるように")
            self.assertEqual(t.get("out_of_scope"), "目次の追加 ⏎ 既存節の書き換え")
            self.assertEqual(t.get("constraints"), "見出し階層は h2 ⏎ を維持")
            # md へ書いて読み戻しても失われない（1 行 = 1 フィールドの規約を壊さない）
            rt = km.parse_task(km.serialize_task(t), t.id)
            self.assertEqual(rt.get("out_of_scope"), "目次の追加 ⏎ 既存節の書き換え")

    def test_build_request_injects_guide_fields(self):
        t = km.Task(id="T1", title="やる", verify="true",
                    extra=[("why", "目的を掴めるように"),
                           ("scope", "README.md のみ"),
                           ("out_of_scope", "目次の追加 ⏎ 既存節の書き換え"),
                           ("constraints", "見出しは h2"),
                           ("hints", "docs/style.md 参照"),
                           ("demo", "冒頭に ## 概要 がある"),
                           ("desc", "冒頭へ概要見出しを足す")])
        req = km.build_request(t)
        self.assertIn("背景・目的", req)
        self.assertIn("目的を掴めるように", req)
        self.assertIn("スコープ", req)
        self.assertIn("やらないこと", req)
        self.assertIn("目次の追加\n  既存節の書き換え", req)   # ⏎ は改行へ戻して読ませる
        self.assertIn("固有の制約", req)
        self.assertIn("実装の手がかり", req)
        self.assertIn("人の確認観点", req)
        self.assertIn("作業内容の詳細", req)
        # verify（完了条件）より前＝タスク本文の一部として提示される
        self.assertLess(req.index("作業内容の詳細"), req.index("完了条件"))

    def test_build_request_without_guide_is_unchanged(self):
        t = km.Task(id="T1", title="やる", verify="true")
        req = km.build_request(t)
        for label in ("背景・目的", "スコープ", "やらないこと", "作業内容の詳細"):
            self.assertNotIn(label, req)

    def test_needs_definition_block_lists_guide_fields(self):
        t = km.Task(id="T1", title="やる", verify="true",
                    extra=[("why", "レビュー者向けの理由"), ("out_of_scope", "隣領域")])
        block = km._task_definition_block(t)
        self.assertIn("- why: レビュー者向けの理由", block)
        self.assertIn("- out_of_scope: 隣領域", block)

    def test_plan_via_agent_carries_why_and_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_charter(d, "# Charter: r\n## goal\nx\n## repos\n"
                          "- app = https://git/app.git\n  - owns: apps/**\n  - base: main\n")
            cfg = cfg_for(d)
            ch = km.load_charter(cfg)
            orig = km._run_agent_cli
            km._run_agent_cli = lambda prompt, model, purpose="": (
                '[{"title":"t","verify":"test -f apps/x","why":"目標に必須",'
                '"out_of_scope":"別領域","hints":"apps/y を参考"}]')
            try:
                specs = km.plan_via_agent(cfg, ch)
            finally:
                km._run_agent_cli = orig
            self.assertEqual(specs[0]["why"], "目標に必須")
            self.assertEqual(specs[0]["out_of_scope"], "別領域")
            self.assertEqual(specs[0]["hints"], "apps/y を参考")
            # planner への指示にも why の要求が入っている
            self.assertIn('"why"', km._plan_decompose_prompt(ch))

    def test_revise_accepts_guide_fields(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1")
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            code = km.cmd_revise(cfg, "T1",
                                 {"why": "背景を明示", "out_of_scope": "隣領域"}, "", "r")
            self.assertEqual(code, 0)
            t = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            self.assertEqual(t.get("why"), "背景を明示")
            self.assertEqual(t.get("out_of_scope"), "隣領域")

    def test_expand_spec_tasks_carries_guide_fields(self):
        # tasks.md（enqueue --json 互換）の誘導・レビュー記述が実装タスクへ落ちずに引き継がれる
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = cfg_for(d, spec_track=True)
            (d / "backlog").mkdir(parents=True, exist_ok=True)
            (d / "backlog" / "T1.md").write_text(
                "## T1: 大きめの機能\n- status: ready\n- verify: `true`\n"
                "- route: spec\n- spec_task: T1-spec\n- after: T1-spec\n", encoding="utf-8")
            adir = cfg.archive_dir()
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "T1-spec.md").write_text(
                "## T1-spec: Spec 作成\n- status: done\n", encoding="utf-8")
            sdir = km.specs_root(cfg) / "T1"
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "tasks.md").write_text(
                '[{"title": "モデルを作る", "verify": "test -f m.py",'
                ' "why": "spec の中核", "out_of_scope": "API 層"}]', encoding="utf-8")
            created = km.expand_spec_tasks(cfg, km.load_tasks(cfg.backlog))
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].get("why"), "spec の中核")
            self.assertEqual(created[0].get("out_of_scope"), "API 層")

    def test_plan_rework_completes_and_preserves_guide_fields(self):
        # 差し戻し修正（AI）が誘導記述を補完・更新でき、応答にキーが無い項目は消えない
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mkb(d, "T1", status="proposed")
            cfg = cfg_for(d)
            cfg.decisions.mkdir(parents=True, exist_ok=True)
            t = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            t.extra.append(("why", "既存の理由"))
            km.persist_task(cfg, t)
            out = ('{"title": "T1", "verify": "true", "scope": "src/ のみ",'
                   ' "out_of_scope": "テスト整備\\nCI 変更"}')   # 生の改行は ⏎ に畳まれる
            with mock.patch.object(km, "_run_agent_cli", return_value=out):
                km.plan_rework(cfg, t, "スコープを src に限定して")
            t2 = next(x for x in km.load_tasks(cfg.backlog) if x.id == "T1")
            self.assertEqual(t2.get("scope"), "src/ のみ")
            self.assertEqual(t2.get("out_of_scope"), "テスト整備 ⏎ CI 変更")
            self.assertEqual(t2.get("why"), "既存の理由")   # キー無し＝既存値を温存
            self.assertIn('"why"', km._plan_rework_prompt(t2, "x"))  # 契約にも載っている

    def test_cohort_propagates_guide_fields(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_for(Path(d))
            pilot = km.enqueue_task(cfg, {
                "title": "{item} を移行", "verify": "test -f {item}",
                "cohort_items": ["a.md", "b.md"],
                "why": "旧形式を廃止するため",
                "constraints": "{item} 以外は変更しない"})
            self.assertEqual(pilot.get("why"), "旧形式を廃止するため")
            self.assertEqual(pilot.get("constraints"), "a.md 以外は変更しない")
            members = km.materialize_cohort_rest(cfg, pilot, feedback="ok")
            self.assertEqual(members[0].get("why"), "旧形式を廃止するため")
            self.assertEqual(members[0].get("constraints"), "b.md 以外は変更しない")
