"""agent-flow の単体テスト — executor（`test_agent_flow.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・共通ヘルパ）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-flow/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class GitlabExecutorPluginTests(unittest.TestCase):
    """gitlab executor プラグイン（opt-in）: イシュー起票 → **関連 MR の状態**をポーリング →
    全マージ＝承認（イシュークローズして成功）/ 一つでも未マージクローズ＝却下（やり直し）。"""

    def setUp(self):
        # ポーリング待ちを無くす設定を環境変数（AGENT_FLOW_EXECUTOR_CONFIG）で渡す
        self._cfg = {"conn_label": "default", "repo_url": "https://gitlab.com/group/repo",
                     "labels": "status:open,assignee:any", "priority": "priority:normal",
                     "poll_interval": 0.0, "timeout": 0.0,
                     "approved_label": "status:approved", "done_label": "status:done"}
        self._prev_env = os.environ.get("AGENT_FLOW_EXECUTOR_CONFIG")
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)
        # deferral モードが残存していると execute が DeferDecision を投げてテストが壊れる
        self._prev_defer = os.environ.pop("AGENT_FLOW_DEFER_WAITS", None)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("AGENT_FLOW_EXECUTOR_CONFIG", None)
        else:
            os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = self._prev_env
        if self._prev_defer is None:
            os.environ.pop("AGENT_FLOW_DEFER_WAITS", None)
        else:
            os.environ["AGENT_FLOW_DEFER_WAITS"] = self._prev_defer

    def _run_with(self, api_side, mrs_seq=None, notes=None, token="glpat-x"):
        """gl_api（issue GET/POST/PUT）と gl_api_list（related_merge_requests / notes）を
        モックして execute を回す。mrs_seq は poll 毎の関連 MR リスト列、notes は人コメント。"""
        mrs_seq = list(mrs_seq or [[]])

        def list_side(host, token_, path, params=None):
            if path.endswith("/related_merge_requests"):
                return mrs_seq.pop(0) if len(mrs_seq) > 1 else mrs_seq[0]
            if path.endswith("/notes"):
                return notes or []
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value=token), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api_side) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            text, data = gl_plugin.execute("work", "ログイン画面を追加", {})
        return text, data, m

    def test_all_mrs_merged_approves_and_closes(self):
        # 関連 MR が全てマージ → 承認。イシューをクローズして成功を返す。
        def api(host, token, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 42, "web_url": "https://gitlab.com/group/repo/-/issues/42"}
            if method == "GET" and path.endswith("/issues/42"):
                return {"labels": ["status:approved"], "state": "opened"}
            if method == "PUT" and path.endswith("/issues/42"):
                return {"state": "closed"}     # クローズ
            return {}

        mrs = [{"iid": 1, "state": "merged", "web_url": "mr1"},
               {"iid": 2, "state": "merged", "web_url": "mr2"}]
        text, data, m = self._run_with(api, mrs_seq=[mrs])
        self.assertEqual(data["decision"], "approved")
        self.assertTrue(data["closed"])
        self.assertEqual(data["merged_mrs"], [1, 2])
        # イシュークローズの PUT（state_event=close）が出る
        close = next(c for c in m.call_args_list if c.args[2] == "PUT")
        self.assertEqual(close.kwargs["data"]["state_event"], "close")
        # 起票の POST も検証
        post = next(c for c in m.call_args_list if c.args[2] == "POST")
        self.assertIn("priority:normal", post.kwargs["data"]["labels"])
        self.assertIn("gitlab-idd:creator-node-id", post.kwargs["data"]["description"])

    def test_one_mr_closed_unmerged_rejects_with_comments(self):
        # 一つでも未マージでクローズ → 却下。人コメントを [gitlab-reject] に載せて送出。
        closed = {"n": 0}

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 9, "web_url": "https://gitlab.com/group/repo/-/issues/9"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            if method == "PUT":
                closed["n"] += 1               # イシュークローズが呼ばれた
                return {"state": "closed"}
            return {}

        mrs = [{"iid": 1, "state": "merged"}, {"iid": 2, "state": "closed"}]  # 一つ却下
        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[mrs],
                           notes=[{"body": "命名が要件と違う。修正して。", "system": False}])
        msg = str(ctx.exception)
        self.assertIn("[gitlab-reject]", msg)
        self.assertIn("命名が要件と違う", msg)        # 人コメントをやり直し指示に活かす
        self.assertEqual(closed["n"], 1)              # 元イシューはクローズされる
        # 承認と対称の機械可読な決着: 例外に data が載る（worker が failed result に書く）
        d = ctx.exception.data
        self.assertEqual(d["decision"], "rejected")
        self.assertEqual(d["issue_iid"], 9)
        self.assertIn("命名が要件と違う", d["guidance"])
        self.assertEqual(d["merged_mrs"], [1])        # マージ済みだった MR も分かる
        self.assertTrue(d["closed"])

    def test_reject_without_comments_says_auto(self):
        # 人コメントが無い却下 → 自動判断の指示で送出
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 3, "web_url": "u"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[{"iid": 1, "state": "closed"}]], notes=[])
        self.assertIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("自動で", str(ctx.exception))

    def test_deleted_issue_treated_as_rejected(self):
        # 決着待ち中にイシューが削除（404）されたら、一般エラーでなく却下（取り下げ）として
        # 決着させる（誤削除でもフィードバックループを壊さない防御）。guidance は空＝自動判断。
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 7, "web_url": "https://gitlab.com/group/repo/-/issues/7"}
            if method == "GET":
                raise RuntimeError("GitLab API GET /projects/x/issues/7 失敗: HTTP 404 Not Found")
            return {}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("削除", str(ctx.exception))
        d = ctx.exception.data
        self.assertEqual(d["decision"], "rejected")
        self.assertIn("削除", d["reason"])
        self.assertEqual(d["guidance"], "")

    def test_non_404_error_still_raises_plain_failure(self):
        # ネットワーク断・権限エラー等（404 以外）は却下でなく従来どおりの失敗として送出
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 7, "web_url": "u"}
            if method == "GET":
                raise RuntimeError("GitLab API GET /projects/x/issues/7 失敗: HTTP 500 Server Error")
            return {}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertNotIn("[gitlab-reject]", str(ctx.exception))
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_open_mr_keeps_waiting_until_merged(self):
        # MR が open のうちは待機し、全マージで承認。
        seq = [[{"iid": 1, "state": "opened"}],          # まだ作業中
               [{"iid": 1, "state": "merged"}]]          # マージ完了
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 8, "web_url": "u"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        text, data, _ = self._run_with(api, mrs_seq=seq)
        self.assertEqual(data["decision"], "approved")

    def test_timeout_raises_before_any_mr(self):
        # MR が一つも出ない（レビュー前）まま全体 timeout 超過で失敗する
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, timeout=0.01, approved_timeout=0.01, poll_interval=0.0))

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 1, "web_url": "https://gitlab.com/group/repo/-/issues/1"}
            return {"labels": ["status:open"], "state": "opened"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]])
        self.assertIn("レビュー/MR 作成", str(ctx.exception))

    # --- 冪等性: 再 claim 時に同じタスクのイシューを二重起票しない ---------------
    def test_task_token_is_deterministic_from_art_dir(self):
        # art_dir（runs/<run>/artifacts/<node>）から決定的トークンを作る。再 claim で同一になる。
        a = gl_plugin._task_token("/bus/runs/r1/artifacts/n1")
        b = gl_plugin._task_token("/other/runs/r1/artifacts/n1")  # 別バスでも同じ run/node
        self.assertTrue(a.startswith("kf-"))
        self.assertEqual(a, b)
        # run か node が違えば別トークン
        self.assertNotEqual(a, gl_plugin._task_token("/bus/runs/r1/artifacts/n2"))
        self.assertNotEqual(a, gl_plugin._task_token("/bus/runs/r2/artifacts/n1"))
        # 想定外の形（art_dir 無し / artifacts 区切りが無い）は None（＝従来どおり毎回新規起票）
        self.assertIsNone(gl_plugin._task_token(None))
        self.assertIsNone(gl_plugin._task_token(""))
        self.assertIsNone(gl_plugin._task_token("/some/random/path"))

    def test_task_token_stable_across_retry_and_revise_suffix(self):
        # inherit_from / revise で run_id 末尾の -rN / -vM が変わっても同一トークン
        # （未決着の open イシューへ再アタッチできる。無関係な run は別トークン）
        a = gl_plugin._task_token("/bus/runs/req-ab12-T1-r0/artifacts/n1")
        b = gl_plugin._task_token("/bus/runs/req-ab12-T1-r3/artifacts/n1")
        c = gl_plugin._task_token("/bus/runs/req-ab12-T1-r3-v2/artifacts/n1")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotEqual(a, gl_plugin._task_token("/bus/runs/req-cd34-T2-r0/artifacts/n1"))

    def test_new_issue_embeds_task_marker(self):
        # art_dir を渡すと本文に隠しマーカーが埋まり、検索（open イシュー）も走る。
        art_dir = "/bus/runs/r1/artifacts/n1"
        token = gl_plugin._task_token(art_dir)

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 7, "web_url": "u7"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        searched = {"n": 0}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # 起票前の重複検索（既存なし）
                searched["n"] += 1
                self.assertEqual(params.get("state"), "opened")
                self.assertEqual(params.get("search"), token)
                return []
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "ログイン画面を追加", {}, art_dir=art_dir)
        self.assertEqual(data["decision"], "approved")
        self.assertEqual(searched["n"], 1)                      # 起票前に検索した
        post = next(c for c in m.call_args_list if c.args[2] == "POST")
        self.assertIn(gl_plugin._task_marker(token), post.kwargs["data"]["description"])

    def test_reattaches_to_existing_open_issue_no_duplicate(self):
        # 再 claim: 同じトークンの open イシューが既にあれば**新規起票せず**再アタッチする。
        art_dir = "/bus/runs/r1/artifacts/n1"
        token = gl_plugin._task_token(art_dir)
        marker = gl_plugin._task_marker(token)

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST":
                raise AssertionError("二重起票してはならない（再アタッチすべき）")
            if method == "GET" and path.endswith("/issues/42"):
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # 既存の open イシューがヒット
                return [{"iid": 42, "web_url": "u42", "description": f"本文\n{marker}"}]
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "ログイン画面を追加", {}, art_dir=art_dir)
        self.assertEqual(data["decision"], "approved")
        self.assertEqual(data["issue_iid"], 42)                # 既存イシューで決着

    def test_search_hit_without_marker_is_ignored(self):
        # 検索が別タスクのイシューを取りこぼし誤ヒットしても、マーカー不一致なら新規起票する。
        art_dir = "/bus/runs/r1/artifacts/n1"

        def api(host, tok, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/issues"):
                return {"iid": 99, "web_url": "u99"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        def list_side(host, tok, path, params=None):
            if path.endswith("/issues"):       # マーカーを持たない別イシュー（誤ヒット）
                return [{"iid": 5, "web_url": "u5", "description": "無関係なイシュー"}]
            if path.endswith("/related_merge_requests"):
                return [{"iid": 1, "state": "merged"}]
            return []

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api) as m, \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            _text, data = gl_plugin.execute("work", "x", {}, art_dir=art_dir)
        self.assertEqual(data["issue_iid"], 99)                # 新規起票で決着
        self.assertTrue(any(c.args[2] == "POST" for c in m.call_args_list))

    def test_mr_decision_helper(self):
        self.assertEqual(gl_plugin._mr_decision(["merged", "merged"]), "approved")
        self.assertEqual(gl_plugin._mr_decision(["merged", "closed"]), "rejected")
        self.assertEqual(gl_plugin._mr_decision(["opened", "merged"]), "")  # 待機
        self.assertEqual(gl_plugin._mr_decision([]), "")                    # MR 無し＝未決着

    # --- イシューが外部でクローズされたときの承認/却下判定 ----------------------
    def test_closed_issue_approved_by_label(self):
        # MR で決着がつかないまま外部クローズ＋status:approved → 承認
        d, why = gl_plugin._closed_issue_decision(
            "h", "t", "p", 1, {"status:approved"}, "status:approved", "status:done")
        self.assertEqual(d, "approved")
        self.assertIn("status:approved", why)

    def test_closed_issue_approved_by_comment(self):
        # ラベル無しでも、コメントが承認を示唆していれば承認
        with mock.patch.object(gl_plugin, "_get_comments",
                               return_value=[{"body": "確認しました。承認します。", "system": False}]):
            d, why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "approved")

    def test_closed_issue_rejected_by_comment(self):
        # コメントが却下を示唆していれば却下（却下語は承認語より優先）
        with mock.patch.object(gl_plugin, "_get_comments",
                               return_value=[{"body": "これは却下。やり直してください。", "system": False}]):
            d, _why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "rejected")

    def test_closed_issue_no_hint_is_withdrawn_reject(self):
        # 手掛かりが無い外部クローズは取り下げ＝却下扱い
        with mock.patch.object(gl_plugin, "_get_comments", return_value=[]):
            d, why = gl_plugin._closed_issue_decision(
                "h", "t", "p", 1, set(), "status:approved", "status:done")
        self.assertEqual(d, "rejected")
        self.assertIn("取り下げ", why)

    def test_externally_closed_with_approve_comment_returns_done(self):
        # execute 全体: MR 無し・外部クローズ・承認コメント → done（成果を返す）でグラフへ反映
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 5, "web_url": "u5"}
            if method == "GET":
                return {"labels": [], "state": "closed"}   # 外部クローズ
            return {"state": "closed"}

        text, data, _ = self._run_with(
            api, mrs_seq=[[]],
            notes=[{"body": "対応ありがとう。承認します。", "system": False}])
        self.assertEqual(data["decision"], "approved")
        self.assertIn("承認", data["reason"])

    def test_externally_closed_with_reject_comment_raises(self):
        # execute 全体: MR 無し・外部クローズ・却下コメント → [gitlab-reject] で送出（failed→やり直し）
        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 6, "web_url": "u6"}
            if method == "GET":
                return {"labels": [], "state": "closed"}
            return {"state": "closed"}

        with self.assertRaises(RuntimeError) as ctx:
            self._run_with(api, mrs_seq=[[]],
                           notes=[{"body": "要件と違うため却下。", "system": False}])
        self.assertIn("[gitlab-reject]", str(ctx.exception))

    def test_missing_repo_url_raises(self):
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(dict(self._cfg, repo_url=""))
        with self.assertRaises(RuntimeError) as ctx:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"):
                gl_plugin.execute("work", "x", {})
        self.assertIn("repo_url", str(ctx.exception))

    def test_missing_token_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value=""):
                gl_plugin.execute("work", "x", {})
        self.assertIn("トークン", str(ctx.exception))

    def test_config_zero_poll_interval_respected(self):
        # 0.0 が `x or default` で 30 に潰れないこと（プラグイン側 _as_float の確認）
        self.assertEqual(gl_plugin._as_float(0.0, 30.0), 0.0)
        self.assertEqual(gl_plugin._as_float(None, 30.0), 30.0)
        self.assertEqual(gl_plugin._as_float("bad", 30.0), 30.0)

    def test_repo_url_default_empty(self):
        os.environ.pop("AGENT_FLOW_EXECUTOR_CONFIG", None)
        self.assertEqual(gl_plugin._config()["repo_url"], "")

    def test_env_override_beats_config_block(self):
        # 個別環境変数 AGENT_FLOW_GITLAB_* が AGENT_FLOW_EXECUTOR_CONFIG より優先される
        os.environ["AGENT_FLOW_GITLAB_CONN_LABEL"] = "work"
        try:
            self.assertEqual(gl_plugin._config()["conn_label"], "work")
        finally:
            os.environ.pop("AGENT_FLOW_GITLAB_CONN_LABEL", None)

    def test_issue_body_has_acceptance_criteria_and_deps(self):
        deps = {"t1": {"output": "上流の成果", "data": {"n": 3}}}
        body = gl_plugin._issue_body("synthesize", "統合する", deps)
        self.assertIn("## 受け入れ条件", body)
        self.assertIn("統合する", body)
        self.assertIn("t1", body)
        self.assertIn("agent-flow", body)


class GitlabAutoMergeTests(unittest.TestCase):
    """自動承認（auto_merge・既定 on）: イシューが status:approved かつ MR がクリーン
    （コンフリクト無し・未解決レビューコメント無し）なら executor がマージしてイシューを
    クローズする（gitlab-review-viewer の承認ボタンと同じ規則）。未クリーンは差し戻す。"""

    def setUp(self):
        self._cfg = {"conn_label": "default", "repo_url": "https://gitlab.com/group/repo",
                     "labels": "status:open,assignee:any", "priority": "priority:normal",
                     "poll_interval": 0.0, "timeout": 0.0,
                     "approved_label": "status:approved", "done_label": "status:done"}
        self._prev_env = os.environ.get("AGENT_FLOW_EXECUTOR_CONFIG")
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("AGENT_FLOW_EXECUTOR_CONFIG", None)
        else:
            os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = self._prev_env

    def _check(self, issue, mrs_seq, mr_detail=None, changes=None, discussions=None,
               expected_target=""):
        """_check_decision を 1 回だけ回す（gl_api / gl_api_list をモック）。
        mrs_seq は related_merge_requests の呼び出し毎のリスト列。calls に API 呼び出しを残す。"""
        mrs_seq = list(mrs_seq)
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, path, data))
            if method == "GET" and "/issues/42" in path:
                return issue
            if method == "GET" and path.endswith("/changes"):
                return {"changes": changes if changes is not None else [{"new_path": "a.py"}]}
            if method == "GET" and "/merge_requests/" in path:
                return mr_detail if mr_detail is not None else \
                    {"merge_status": "can_be_merged", "has_conflicts": False}
            if method == "PUT" and path.endswith("/merge"):
                return {"state": "merged"}
            return {"state": "closed"}

        def list_side(host, token, path, params=None):
            if path.endswith("/related_merge_requests"):
                return mrs_seq.pop(0) if len(mrs_seq) > 1 else mrs_seq[0]
            if path.endswith("/discussions"):
                return discussions or []
            return []

        with mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            r = gl_plugin._check_decision("gitlab.com", "tok", "group/repo", 42, "u42",
                                          gl_plugin._config(), False, expected_target)
        return r, calls

    def test_approved_clean_mr_is_merged_and_issue_closed(self):
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened", "project_id": 55}]
        merged = [{"iid": 1, "state": "merged", "project_id": 55, "web_url": "mr1"}]
        r, calls = self._check(issue, [opened, merged])
        self.assertEqual(r["decision"], "approved")
        # マージは should_remove_source_branch 付き・MR 自身のプロジェクト（project_id）へ
        merge = next(c for c in calls if c[1].endswith("/merge"))
        self.assertIn("/projects/55/merge_requests/1/merge", merge[1])
        self.assertTrue(merge[2]["should_remove_source_branch"])
        # イシューはクローズされ、成果テキストに自動承認の根拠が残る
        close = next(c for c in calls if c[0] == "PUT" and "/issues/42" in c[1])
        self.assertEqual(close[2]["state_event"], "close")
        self.assertIn("自動承認", r["text"])

    def test_approved_with_unresolved_discussion_sends_back(self):
        issue = {"labels": ["status:approved", "assignee:any"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]
        discussions = [{"notes": [{"resolvable": True, "resolved": False}]}]
        r, calls = self._check(issue, [opened], discussions=discussions)
        self.assertIsNone(r["decision"])                     # 決着せず（修正待ち）
        # ラベルが approved → needs-rework に付け替わる
        relabel = next(c for c in calls if c[0] == "PUT" and "/issues/42" in c[1]
                       and c[2] and "labels" in c[2])
        self.assertIn("status:needs-rework", relabel[2]["labels"])
        self.assertNotIn("status:approved", relabel[2]["labels"])
        # `# 差し戻し` 見出しの固定コメントが付く
        note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
        self.assertIn("差し戻し", note[2]["body"])
        self.assertNotIn("/merge", str([c[1] for c in calls if c[0] == "PUT"]))  # マージしない

    def test_approved_with_conflicts_sends_back(self):
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]
        detail = {"merge_status": "cannot_be_merged", "has_conflicts": True}
        r, calls = self._check(issue, [opened], mr_detail=detail)
        self.assertIsNone(r["decision"])
        note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
        self.assertIn("コンフリクト", note[2]["body"])

    def test_approved_mr_wrong_target_branch_sends_back(self):
        # ワークスペースの target は develop なのに MR が main を狙っている → 自動マージしない
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]
        detail = {"merge_status": "can_be_merged", "has_conflicts": False,
                  "target_branch": "main"}
        r, calls = self._check(issue, [opened], mr_detail=detail, expected_target="develop")
        self.assertIsNone(r["decision"])                     # 別ブランチ向け＝決着させない
        # マージは呼ばれない
        self.assertNotIn("/merge", str([c[1] for c in calls if c[0] == "PUT"]))
        # 差し戻しコメントに期待ターゲットと実ターゲットが出る
        note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
        self.assertIn("develop", note[2]["body"])
        self.assertIn("main", note[2]["body"])
        # approved → needs-rework に付け替わる
        relabel = next(c for c in calls if c[0] == "PUT" and "/issues/42" in c[1]
                       and c[2] and "labels" in c[2])
        self.assertIn("status:needs-rework", relabel[2]["labels"])

    def test_approved_mr_matching_target_branch_is_merged(self):
        # MR の target がワークスペースの target と一致 → 従来どおり自動マージ
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened", "project_id": 55}]
        merged = [{"iid": 1, "state": "merged", "project_id": 55, "web_url": "mr1"}]
        detail = {"merge_status": "can_be_merged", "has_conflicts": False,
                  "target_branch": "develop"}
        r, calls = self._check(issue, [opened, merged], mr_detail=detail,
                               expected_target="develop")
        self.assertEqual(r["decision"], "approved")
        merge = next(c for c in calls if c[1].endswith("/merge"))
        self.assertIn("/projects/55/merge_requests/1/merge", merge[1])

    def test_approved_mr_target_not_validated_when_target_unknown(self):
        # ワークスペース未指定（expected_target="")なら target 検証はスキップ＝従来挙動（後方互換）
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened", "project_id": 55}]
        merged = [{"iid": 1, "state": "merged", "project_id": 55}]
        detail = {"merge_status": "can_be_merged", "has_conflicts": False,
                  "target_branch": "main"}
        r, calls = self._check(issue, [opened, merged], mr_detail=detail, expected_target="")
        self.assertEqual(r["decision"], "approved")          # 検証しないのでマージされる
        self.assertTrue(any(c[1].endswith("/merge") for c in calls))

    def test_approved_no_diff_mr_is_closed_not_merged(self):
        # 差分なし MR（取り込み済み等）はマージでなくクローズ＋ソースブランチ削除で決着
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened", "source_branch": "af/run-1"}]
        closed = [{"iid": 1, "state": "closed"}]   # クローズ後の再取得
        r, calls = self._check(issue, [opened, closed], changes=[])
        self.assertEqual(r["decision"], "approved")
        self.assertIn("差分なし", r["text"])
        self.assertFalse(any(c[1].endswith("/merge") for c in calls))       # マージしない
        close_mr = next(c for c in calls if c[0] == "PUT" and "/merge_requests/1" in c[1])
        self.assertEqual(close_mr[2]["state_event"], "close")
        self.assertTrue(any(c[0] == "DELETE" and "/repository/branches/" in c[1]
                            for c in calls))                                  # ブランチ削除
        # 却下（未マージクローズ）ではなく承認として決着している
        self.assertIn("自動承認", r["text"])

    def test_approved_without_mr_closes_issue_as_approved(self):
        issue = {"labels": ["status:approved"], "state": "opened"}
        r, calls = self._check(issue, [[]])
        self.assertEqual(r["decision"], "approved")
        self.assertIn("MR 無し", r["text"])

    def test_auto_merge_off_keeps_waiting(self):
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, auto_merge=False))
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]
        r, calls = self._check(issue, [opened])
        self.assertIsNone(r["decision"])                     # 従来どおり人のマージ待ち
        self.assertFalse(any(c[1].endswith("/merge") for c in calls))

    def test_not_approved_keeps_waiting(self):
        issue = {"labels": ["status:in-progress"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]
        r, calls = self._check(issue, [opened])
        self.assertIsNone(r["decision"])
        self.assertFalse(any(c[1].endswith("/merge") for c in calls))

    def test_merge_api_failure_keeps_waiting_for_retry(self):
        # マージ 403/405 等は決着させず次のポーリングで再試行（run を殺さない）
        issue = {"labels": ["status:approved"], "state": "opened"}
        opened = [{"iid": 1, "state": "opened"}]

        def api(host, token, method, path, data=None, params=None):
            if method == "GET" and "/issues/42" in path:
                return issue
            if method == "GET" and path.endswith("/changes"):
                return {"changes": [{"new_path": "a.py"}]}
            if method == "GET" and "/merge_requests/" in path:
                return {"merge_status": "can_be_merged", "has_conflicts": False}
            if method == "PUT" and path.endswith("/merge"):
                raise RuntimeError("GitLab API PUT .../merge 失敗: HTTP 403 Forbidden")
            return {}

        def list_side(host, token, path, params=None):
            return [] if path.endswith("/discussions") else opened

        with mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            r = gl_plugin._check_decision("gitlab.com", "tok", "group/repo", 42, "u42",
                                          gl_plugin._config(), False)
        self.assertIsNone(r["decision"])                     # 未決着のまま（人の介入も可能）

    def test_midflight_rework_rejects_without_closing_issue(self):
        # 人の `# 差し戻し` 見出し → rejected だがイシューは open のまま（終端却下と違う）
        issue = {"labels": ["status:open"], "state": "opened"}
        notes = [{"id": 42, "body": "# 差し戻し\nlocalhost ではなく実サーバで",
                  "system": False, "author": {"username": "alice", "id": 1}}]
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, path, data))
            if method == "GET" and "/issues/42" in path:
                return issue
            return {"ok": True}

        def list_side(host, token, path, params=None):
            if path.endswith("/related_merge_requests"):
                return []
            return []

        with mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side), \
             mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            r = gl_plugin._check_decision("gitlab.com", "tok", "group/repo", 42, "u42",
                                          gl_plugin._config(), False)
        self.assertEqual(r["decision"], "rejected")
        self.assertFalse(r["data"]["closed"])
        self.assertTrue(r["data"].get("rework"))
        self.assertIn("実サーバ", r["data"]["guidance"])
        # state_event=close していない
        self.assertFalse(any(
            c[0] == "PUT" and (c[2] or {}).get("state_event") == "close" for c in calls))
        # needs-rework ラベル付け替えはある
        relabel = next(c for c in calls if c[0] == "PUT" and "/issues/42" in c[1]
                       and c[2] and "labels" in c[2])
        self.assertIn("status:needs-rework", relabel[2]["labels"])

    # --- close_issues: manual（クローズは人。人のクローズを監視して決着） ---

    def _check_manual(self, issue, mrs, notes=None):
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            dict(self._cfg, close_issues="manual"))
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, path, data))
            if method == "GET" and "/issues/42" in path:
                return issue
            return {}

        def list_side(host, token, path, params=None):
            if path.endswith("/related_merge_requests"):
                return mrs
            if path.endswith("/notes"):
                return notes or []
            return []

        with mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
            r = gl_plugin._check_decision("gitlab.com", "tok", "group/repo", 42, "u42",
                                          gl_plugin._config(), False)
        return r, calls

    def test_manual_close_waits_after_all_merged_and_posts_note_once(self):
        # 全 MR マージ済みでも issue が open なら決着せず、案内ノートを投稿して人のクローズを待つ
        issue = {"labels": [], "state": "opened"}
        mrs = [{"iid": 1, "state": "merged"}]
        r, calls = self._check_manual(issue, mrs)
        self.assertIsNone(r["decision"])                     # クローズは人 → 未決着
        note = next(c for c in calls if c[0] == "POST" and c[1].endswith("/notes"))
        self.assertIn("クローズしてください", note[2]["body"])
        self.assertIn("agent-flow:close-request", note[2]["body"])
        # イシューをクローズする PUT は出ない
        self.assertFalse(any(c[0] == "PUT" and "/issues/42" in c[1] for c in calls))

    def test_manual_close_note_is_idempotent(self):
        # 既にマーカー付きノートがあれば再投稿しない
        issue = {"labels": [], "state": "opened"}
        mrs = [{"iid": 1, "state": "merged"}]
        notes = [{"body": "agent-flow: …クローズしてください <!-- agent-flow:close-request -->",
                  "system": False}]
        r, calls = self._check_manual(issue, mrs, notes=notes)
        self.assertIsNone(r["decision"])
        self.assertFalse(any(c[0] == "POST" and c[1].endswith("/notes") for c in calls))

    def test_manual_close_resolves_when_human_closes(self):
        # 人がクローズ → 全マージ＝承認として決着（既存の issue_closed 経路）
        issue = {"labels": [], "state": "closed"}
        mrs = [{"iid": 1, "state": "merged"}]
        r, _calls = self._check_manual(issue, mrs)
        self.assertEqual(r["decision"], "approved")


class GitlabNativeApiTests(unittest.TestCase):
    """native レイヤ（URL 解析・REST 組立・トークン解決）の単体検証。"""

    _TOKEN_ENV = ("GITLAB_TOKEN", "GL_TOKEN")

    def setUp(self):
        self._prev_tok = {k: os.environ.pop(k, None) for k in self._TOKEN_ENV}

    def tearDown(self):
        for k, v in self._prev_tok.items():
            if v is not None:
                os.environ[k] = v

    def test_parse_project_url_variants(self):
        self.assertEqual(gl_plugin._parse_project_url("https://gitlab.com/g/r.git"),
                         ("gitlab.com", "g/r"))
        self.assertEqual(gl_plugin._parse_project_url("https://gl.example.com/a/b/c/"),
                         ("gl.example.com", "a/b/c"))
        # self-host の別ポートはポートを保つ（落とすと API が到達できない）
        self.assertEqual(gl_plugin._parse_project_url("http://gitlab.local:8929/g/r.git"),
                         ("gitlab.local:8929", "g/r"))
        self.assertEqual(gl_plugin._parse_project_url("not-a-url"), (None, None))

    def test_resolve_project_requires_repo_url(self):
        with self.assertRaises(RuntimeError) as ctx:
            gl_plugin._resolve_project({"repo_url": ""})
        self.assertIn("repo_url", str(ctx.exception))

    def test_resolve_project_parses_ssh_url(self):
        # SSH 形のワークスペース URL も起票先として解釈できる（パス・末尾 .git を剥がす）。
        # scheme を持たないためベースはホスト名のみ（gl_api が https を既定にする）
        base, project, _ = gl_plugin._resolve_project(
            {}, workspace_url="git@gitlab.com:group/repo.git")
        self.assertEqual((base, project), ("gitlab.com", "group/repo"))

    def test_resolve_project_parses_repo_url(self):
        base, project, repo_url = gl_plugin._resolve_project(
            {"repo_url": "https://gitlab.com/group/sub/repo"})
        self.assertEqual((base, project), ("https://gitlab.com", "group/sub/repo"))

    def test_resolve_project_prefers_workspace_over_config(self):
        # ワークスペース URL が config の repo_url より優先される（その run の唯一の書込先へ起票）
        base, project, used = gl_plugin._resolve_project(
            {"repo_url": "https://gitlab.com/fallback/repo"},
            workspace_url="https://gitlab.com/team/app")
        self.assertEqual((base, project), ("https://gitlab.com", "team/app"))
        self.assertEqual(used, "https://gitlab.com/team/app")

    def test_resolve_project_keeps_http_scheme_and_port(self):
        # http の self-host（local-gitlab-stack 等）は scheme とポートを保つ。
        # 従来は https://<hostのみ> に強制されて「接続できません」になっていた
        # （エラーに出るパスの %2F は正規エンコードで無関係）。
        base, project, _ = gl_plugin._resolve_project(
            {"repo_url": "http://gitlab.local/group/repo.git"})
        self.assertEqual((base, project), ("http://gitlab.local", "group/repo"))
        base, project, _ = gl_plugin._resolve_project(
            {}, workspace_url="http://gitlab.local:8929/team/app")
        self.assertEqual((base, project), ("http://gitlab.local:8929", "team/app"))

    def test_resolve_project_api_base_override(self):
        # SSH 形しか無い + API は http/別ポート、の構成は api_base で明示できる（最優先）
        base, project, _ = gl_plugin._resolve_project(
            {"api_base": "http://gitlab.local:8929/"},
            workspace_url="git@gitlab.local:group/repo.git")
        self.assertEqual((base, project), ("http://gitlab.local:8929", "group/repo"))

    def test_gl_api_builds_v4_request(self):
        captured = {}

        class _Resp:
            headers = {}

            def read(self):
                return b'{"iid": 1}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["token"] = req.headers.get("Private-token")
            return _Resp()

        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = gl_plugin.gl_api("gitlab.com", "glpat-x", "GET", "/projects/1/issues/2")
        self.assertEqual(out, {"iid": 1})
        self.assertEqual(captured["url"], "https://gitlab.com/api/v4/projects/1/issues/2")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["token"], "glpat-x")
        # scheme 付きベース（http self-host・別ポート）はそのまま使う（https に強制しない）
        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            gl_plugin.gl_api("http://gitlab.local:8929", "glpat-x", "GET", "/projects/1")
        self.assertEqual(captured["url"], "http://gitlab.local:8929/api/v4/projects/1")

    def test_gl_api_connection_error_shows_full_url(self):
        # 接続不能のエラーは完全な URL を出す（scheme/ポートの取り違えを診断できる。
        # パス中の %2F は GitLab API の正規エンコードで、接続可否とは無関係）
        def fake_urlopen(req, timeout=None):
            raise gl_plugin.urllib.error.URLError("connection refused")

        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                gl_plugin.gl_api("http://gitlab.local:8929", "t", "GET",
                                 "/projects/team%2Fapp/issues")
        self.assertIn("http://gitlab.local:8929/api/v4/projects/team%2Fapp/issues",
                      str(ctx.exception))

    def test_gl_api_http_error_raises_runtimeerror(self):
        def fake_urlopen(req, timeout=None):
            raise gl_plugin.urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"404"}'))

        with mock.patch.object(gl_plugin.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                gl_plugin.gl_api("gitlab.com", "t", "GET", "/projects/1")
        self.assertIn("404", str(ctx.exception))

    def test_token_prefers_connections_yaml(self):
        # gl.py と同じく connections.yaml（接続ラベル）を最優先で読む
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value="tok-conn"), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            os.environ["GITLAB_TOKEN"] = "tok-env"
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-conn")

    def test_token_env_fallback(self):
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            os.environ["GITLAB_TOKEN"] = "tok-env"
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-env")

    def test_token_shell_fallback(self):
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value="tok-shell"):
            self.assertEqual(gl_plugin._resolve_token({"conn_label": "default"}), "tok-shell")

    def test_token_not_read_from_agent_flow_yaml(self):
        # agent-flow.yaml 由来の cfg に token を置いても無視される（gl.py の場所だけを読む）
        with mock.patch.object(gl_plugin, "_token_from_connections", return_value=""), \
             mock.patch.object(gl_plugin, "_token_from_shell_files", return_value=""):
            self.assertEqual(
                gl_plugin._resolve_token({"conn_label": "default", "token": "glpat-yaml"}), "")

    def test_token_from_connections_no_scripts_dir(self):
        with mock.patch.object(gl_plugin, "_find_gitlab_idd_scripts_dir", return_value=None):
            self.assertEqual(gl_plugin._token_from_connections("default"), "")


class CallExecutorDispatchTests(unittest.TestCase):
    """call_executor: clone 指示（repo_instruction）を goal に結合せず別引数で渡す。
    受け取れない旧 executor には従来どおり goal 先頭へ結合する（後方互換）。"""

    INSTR = "【成果物リポジトリ】… /tmp/clone/x"

    def test_accepts_detection(self):
        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            return "ok", None

        def legacy_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            return "ok", None

        def kwargs_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None, **kw):
            return "ok", None

        self.assertTrue(kf._executor_accepts(new_exec, "repo_instruction"))
        self.assertTrue(kf._executor_accepts(kwargs_exec, "repo_instruction"))
        self.assertFalse(kf._executor_accepts(legacy_exec, "repo_instruction"))

    def test_new_executor_gets_clean_goal_and_instruction(self):
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            seen["goal"] = goal
            seen["instr"] = repo_instruction
            return "ok", None

        kf.call_executor(new_exec, "work", "本来のゴール", {}, None, None, None, self.INSTR)
        self.assertEqual(seen["goal"], "本来のゴール")        # goal は汚れない
        self.assertEqual(seen["instr"], self.INSTR)

    def test_legacy_executor_gets_prepended_goal(self):
        seen = {}

        def legacy_exec(kind, goal, dep_results, model, art_dir=None, dep_arts=None):
            seen["goal"] = goal
            return "ok", None

        kf.call_executor(legacy_exec, "work", "本来のゴール", {}, None, None, None, self.INSTR)
        self.assertTrue(seen["goal"].startswith(self.INSTR))   # 旧契約は従来どおり結合
        self.assertIn("本来のゴール", seen["goal"])

    def test_no_instruction_passes_goal_unchanged(self):
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repo_instruction=""):
            seen["goal"], seen["instr"] = goal, repo_instruction
            return "ok", None

        kf.call_executor(new_exec, "work", "ゴール", {}, None, None, None, "")
        self.assertEqual(seen["goal"], "ゴール")
        self.assertEqual(seen["instr"], "")

    def test_execute_agent_puts_instruction_in_prompt_not_polluting_goal(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.call_executor(kf.execute_agent, "work", "ログイン追加", {}, None, None, None,
                             self.INSTR)
        # タスク行の goal は本来のゴールのまま、clone 指示はプロンプト内に別途含まれる
        self.assertIn("タスク(work): ログイン追加", captured["prompt"])
        self.assertIn(self.INSTR, captured["prompt"])

    def test_execute_agent_runs_in_workspace_root(self):
        captured = {}

        def fake_run_agent(prompt, model, purpose="", cwd=None, **_kw):
            captured["cwd"] = cwd
            return "成果"

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "実装", {}, None,
                             workspace={"clone": "/tmp/agent-flow-workspace"})
        self.assertEqual(captured["cwd"], "/tmp/agent-flow-workspace")

    def test_deps_data_is_injected_as_compact_json_by_default(self):
        """案 K-1（docs/plans/2026-08-05-json-prompt-compression-study.md）: deps の構造化 data
        は既定でも indent 無し・区切り最小の compact JSON（内容は不変）。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        dep_results = {"t1": {"output": "ok", "data": {"items": [{"id": "a"}, {"id": "b"}]}}}
        # flow-worker スキルを無効化して組み込み fallback プロンプトを通す
        # （インストール済みスキルの prompt.py は compact 描画に未対応でも壊れない設計のため）
        with mock.patch.object(kf, "_flow_worker_prompt", return_value=None), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("synthesize", "統合", dep_results, None)
        self.assertIn('data: {"items":[{"id":"a"},{"id":"b"}]}', captured["prompt"])

    def test_deps_data_folds_to_table_when_prompt_table_is_on(self):
        """案 K-2（オプトイン）: prompt_table=True のときだけ均質配列を表形式へ畳む。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        dep_results = {"t1": {"output": "ok", "data": {"items": [{"id": "a"}, {"id": "b"}]}}}
        with mock.patch.object(kf, "_flow_worker_prompt", return_value=None), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("synthesize", "統合", dep_results, None, prompt_table=True)
        self.assertIn("items[2]{id}:", captured["prompt"])

    def test_call_executor_forwards_prompt_table_only_when_accepted(self):
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, prompt_table=False):
            seen["prompt_table"] = prompt_table
            return "ok", None

        def legacy_exec(kind, goal, dep_results, model, art_dir, dep_arts):
            seen["called"] = True
            return "ok", None

        kf.call_executor(new_exec, "work", "g", {}, None, None, None, prompt_table=True)
        self.assertTrue(seen["prompt_table"])
        seen.clear()
        kf.call_executor(legacy_exec, "work", "g", {}, None, None, None, prompt_table=True)
        self.assertTrue(seen["called"])  # 受け取れない executor には渡さず、壊れずに呼べる

    def test_call_executor_forwards_repair_only_when_accepted(self):
        """案 B-1: repair は受け取れる executor にだけ渡り、受け取れない旧 executor でも壊れない。"""
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, repair=None):
            seen["repair"] = repair
            return "ok", None

        def legacy_exec(kind, goal, dep_results, model, art_dir, dep_arts):
            seen["called"] = True
            return "ok", None

        repair = {"of": "t1", "output": "前回の成果", "issues": ["直せ"],
                  "artifact_dir": None, "delivered": False}
        kf.call_executor(new_exec, "work", "g", {}, None, None, None, repair=repair)
        self.assertEqual(seen["repair"], repair)
        seen.clear()
        kf.call_executor(legacy_exec, "work", "g", {}, None, None, None, repair=repair)
        self.assertTrue(seen["called"])

    def test_execute_agent_renders_repair_brief_in_prompt(self):
        """案 B-1: repair があれば、指摘・前回成果の抜粋・成果物パス・反映済みの一文が
        プロンプトに載る。全体を作り直させない指示も含む。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        repair = {"of": "t1-work", "output": "前回はここまでできた",
                  "issues": ["境界値の扱いが違う", "テストが1件失敗"],
                  "artifact_dir": "/tmp/bus/artifacts/t1-work", "delivered": True}
        # repair_note 未対応のスキルが入っていても壊れない設計のため、組み込み経路で検証する
        with mock.patch.object(kf, "_flow_worker_prompt", return_value=None), \
             mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None, repair=repair)
        prompt = captured["prompt"]
        self.assertIn("前回 t1-work として実行され", prompt)
        self.assertIn("境界値の扱いが違う", prompt)
        self.assertIn("テストが1件失敗", prompt)
        self.assertIn("前回の成果（抜粋）: 前回はここまでできた", prompt)
        self.assertIn("/tmp/bus/artifacts/t1-work", prompt)
        self.assertIn("作業ツリーの現状が前回の結果です", prompt)
        self.assertIn("全体を作り直さず", prompt)

    def test_execute_agent_without_repair_is_byte_identical(self):
        """オプトイン既定（repair=None）ではプロンプトが従来と 1 バイトも変わらない。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None)
        without_repair = captured["prompt"]
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None, repair=None)
        self.assertEqual(without_repair, captured["prompt"])
        self.assertNotIn("前回の試行", without_repair)

    def test_call_executor_forwards_context_only_when_accepted(self):
        """案 H: context は受け取れる executor にだけ渡り、受け取れない旧 executor でも壊れない。"""
        seen = {}

        def new_exec(kind, goal, dep_results, model, art_dir, dep_arts, context=""):
            seen["context"] = context
            return "ok", None

        def legacy_exec(kind, goal, dep_results, model, art_dir, dep_arts):
            seen["called"] = True
            return "ok", None

        kf.call_executor(new_exec, "work", "g", {}, None, None, None, context="CTX")
        self.assertEqual(seen["context"], "CTX")
        seen.clear()
        kf.call_executor(legacy_exec, "work", "g", {}, None, None, None, context="CTX")
        self.assertTrue(seen["called"])

    def test_execute_agent_prepends_context_ahead_of_instructions(self):
        """案 H: [instructions][context][本体] の順で前置される。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None,
                             instructions="INSTR-BLOCK", context="CTX-BLOCK")
        prompt = captured["prompt"]
        self.assertIn("INSTR-BLOCK", prompt)
        self.assertIn("CTX-BLOCK", prompt)
        self.assertLess(prompt.index("INSTR-BLOCK"), prompt.index("CTX-BLOCK"))
        self.assertLess(prompt.index("CTX-BLOCK"), prompt.index("タスク(work): 修正して"))

    def test_execute_agent_without_context_is_byte_identical(self):
        """オプトイン既定（context=""）ではプロンプトが従来と 1 バイトも変わらない。"""
        captured = {}

        def fake_run_agent(prompt, model, purpose="", **_kw):
            captured["prompt"] = prompt
            return "成果"

        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None)
        without_context = captured["prompt"]
        with mock.patch.object(kf, "run_agent", side_effect=fake_run_agent):
            kf.execute_agent("work", "修正して", {}, None, context="")
        self.assertEqual(without_context, captured["prompt"])


class GitlabRepoInstructionTests(unittest.TestCase):
    """gitlab: clone 指示はイシュー本文の独立節に載せ、タイトル/目的は本来の goal を保つ。"""

    def test_issue_body_renders_workspace_as_markdown(self):
        ws = {"url": "https://git/app.git", "path": "apps/api", "base": "main",
              "target": "develop", "desc": "API", "branch": "af/run-1",
              "clone": "/tmp/agent-flow-ws-123/app"}
        body = gl_plugin._issue_body("work", "ログイン画面を追加", {}, ws)
        self.assertIn("## 目的", body)
        self.assertIn("## 対象リポジトリ", body)
        # 構造化 Markdown 箇条書き（レイアウトが崩れない）
        self.assertIn("- **リポジトリ**: https://git/app.git", body)
        self.assertIn("- **変更対象フォルダ**: `apps/api` 配下のみ", body)
        self.assertIn("`main` から分岐", body)
        self.assertIn("`develop` へ MR", body)
        # ローカルの作業ディレクトリ（clone パス）はリモートには無いので載せない
        self.assertNotIn("作業ディレクトリ", body)
        self.assertNotIn("/tmp/agent-flow-ws-123/app", body)
        # 目的節は本来の goal のまま
        purpose = body.split("## 目的", 1)[1].split("##", 1)[0]
        self.assertIn("ログイン画面を追加", purpose)

    def test_issue_body_omits_section_when_no_workspace(self):
        body = gl_plugin._issue_body("work", "ゴール", {})
        self.assertNotIn("## 対象リポジトリ", body)
        self.assertNotIn("## 参照リポジトリ", body)

    def test_issue_body_renders_reference_section(self):
        # 参照リポジトリ（読むだけ）がイシュー本文に独立節として載る
        refs = [{"url": "https://git/spec.git", "path": "openapi", "base": "main", "desc": "API 仕様"},
                {"url": "https://git/lib.git"}]
        body = gl_plugin._issue_body("work", "実装する", {}, None, refs)
        self.assertIn("## 参照リポジトリ", body)
        self.assertIn("- **https://git/spec.git**", body)
        self.assertIn("フォルダ `openapi`", body)
        self.assertIn("API 仕様", body)
        self.assertIn("- **https://git/lib.git**", body)
        self.assertIn("読み取り専用", body)

    def test_execute_renders_workspace_section_without_clone_path(self):
        ws = {"url": "https://gitlab.com/group/repo.git", "base": "main", "target": "main",
              "clone": "/tmp/agent-flow-ws-9/repo"}
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, data))
            if method == "POST":
                return {"iid": 3, "web_url": "https://gitlab.com/group/repo/-/issues/3"}
            if method == "PUT":
                return {"state": "closed"}
            return {"labels": ["status:approved"], "state": "opened"}

        def list_side(host, token, path, params=None):
            return [{"iid": 1, "state": "merged"}] if path.endswith(
                "/related_merge_requests") else []   # 全マージ＝承認

        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(
            {"repo_url": "https://gitlab.com/group/repo", "poll_interval": 0.0, "timeout": 0.0})
        try:
            with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
                 mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
                 mock.patch.object(gl_plugin, "gl_api_list", side_effect=list_side):
                # 全 MR マージ＝承認でクローズして完了。workspace 節がイシュー本文に出る。
                gl_plugin.execute("work", "ログイン画面を追加", {}, workspace=ws,
                                  repo_instruction="【ワークスペース】… /tmp/agent-flow-ws-9/repo")
        finally:
            os.environ.pop("AGENT_FLOW_EXECUTOR_CONFIG", None)
        post = next(c for c in calls if c[0] == "POST")
        # タイトルは本来の goal
        self.assertIn("ログイン画面を追加", post[1]["title"])
        # 本文は構造化された対象リポジトリ節（ローカル clone パスは載らない）
        self.assertIn("## 対象リポジトリ", post[1]["description"])
        self.assertNotIn("/tmp/agent-flow-ws-9/repo", post[1]["description"])
        self.assertNotIn("作業ディレクトリ", post[1]["description"])
        purpose = post[1]["description"].split("## 目的", 1)[1].split("##", 1)[0]
        self.assertIn("ログイン画面を追加", purpose)


class ExecutorResolutionTests(unittest.TestCase):
    """executor のプラグイン解決（agent-loop の event_hook 流のローダ）。"""

    def _args(self, **kw):
        import types
        return types.SimpleNamespace(**kw)

    def test_builtin_agent_and_stub(self):
        self.assertIs(kf.make_executor(self._args(executor="agent")), kf.execute_agent)
        self.assertIs(kf.make_executor(self._args(executor="stub")), kf.execute_stub)

    def test_default_is_agent(self):
        self.assertIs(kf.make_executor(self._args(executor=None)), kf.execute_agent)

    def test_resolves_bundled_gitlab_plugin(self):
        fn = kf.make_executor(self._args(executor="gitlab", gitlab={"poll_interval": 1}))
        self.assertTrue(callable(fn))
        # プラグイン設定が環境変数で渡されていること
        self.assertEqual(json.loads(os.environ["AGENT_FLOW_EXECUTOR_CONFIG"]),
                         {"poll_interval": 1})

    def test_explicit_path_plugin(self):
        path = str(HERE.parent / "executors" / "gitlab.py")
        fn = kf.make_executor(self._args(executor=path))
        self.assertTrue(callable(fn))

    def test_resolve_executor_config_json(self):
        # 組み込み executor は設定ブロック無し → None
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="agent")))
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor=None)))
        # プラグイン executor は同名ブロックを JSON 化
        js = kf.resolve_executor_config_json(
            self._args(executor="gitlab", gitlab={"repo_url": "u", "conn_label": "c"}))
        self.assertEqual(json.loads(js), {"repo_url": "u", "conn_label": "c"})
        # ブロックが無い/空なら None（親の値を上書きしない判断に使う）
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="gitlab", gitlab=None)))
        self.assertIsNone(kf.resolve_executor_config_json(self._args(executor="gitlab", gitlab={})))

    def test_unresolvable_executor_exits(self):
        with self.assertRaises(SystemExit):
            kf.make_executor(self._args(executor="does-not-exist-xyz"))

    def test_plugin_missing_execute_exits(self):
        # execute() を持たないダミープラグインを一時生成して読み込ませる
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "noexec.py"
            p.write_text("X = 1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                kf.make_executor(self._args(executor=str(p)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_module_cache_reloads_on_mtime_change(self):
        d = tempfile.mkdtemp(prefix="kf-exec-")
        try:
            p = pathlib.Path(d) / "p.py"
            p.write_text("VALUE = 1\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            m1 = kf._load_executor_module(str(p))
            self.assertEqual(m1.VALUE, 1)
            # mtime を進めて内容を変える → 再ロードされること
            time.sleep(0.01)
            p.write_text("VALUE = 2\ndef execute(*a, **k):\n    return ('', None)\n",
                         encoding="utf-8")
            os.utime(p, (time.time() + 5, time.time() + 5))
            m2 = kf._load_executor_module(str(p))
            self.assertEqual(m2.VALUE, 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class GitlabDeferPollTests(unittest.TestCase):
    """gitlab executor: deferral（park）・poll・on_cancel の追加契約。"""

    def setUp(self):
        self._cfg = {"repo_url": "https://gitlab.com/group/repo", "poll_interval": 0.0,
                     "timeout": 0.0, "approved_timeout": 0.0}
        self._prev = os.environ.get("AGENT_FLOW_EXECUTOR_CONFIG")
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = json.dumps(self._cfg)
        self._prev_defer = os.environ.get("AGENT_FLOW_DEFER_WAITS")

    def tearDown(self):
        for k, v in (("AGENT_FLOW_EXECUTOR_CONFIG", self._prev),
                     ("AGENT_FLOW_DEFER_WAITS", self._prev_defer)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_execute_defers_when_undecided(self):
        # DEFER 有効かつ未決着（MR 無し）→ ブロックせず DeferDecision を投げる
        os.environ["AGENT_FLOW_DEFER_WAITS"] = "1"

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 11, "web_url": "https://gitlab.com/group/repo/-/issues/11"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {}

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", return_value=[]):
            with self.assertRaises(gl_plugin.DeferDecision) as ctx:
                gl_plugin.execute("work", "ログイン画面", {}, art_dir="/b/runs/r1/artifacts/n1")
        d = ctx.exception.defer
        self.assertEqual(d["executor"], "gitlab")
        self.assertEqual(d["issue"]["iid"], 11)
        self.assertFalse(d["throttled"])
        self.assertTrue(d["task_token"].startswith("kf-"))

    def test_execute_defer_returns_immediately_when_approved(self):
        # DEFER 有効でも、その場で承認済みなら park せず (text, data) を返す
        os.environ["AGENT_FLOW_DEFER_WAITS"] = "1"

        def api(host, token, method, path, data=None, params=None):
            if method == "POST":
                return {"iid": 12, "web_url": "u"}
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list",
                               side_effect=lambda h, t, p, params=None:
                               [{"iid": 1, "state": "merged"}] if p.endswith("merge_requests") else []):
            text, data = gl_plugin.execute("work", "g", {})
        self.assertEqual(data["decision"], "approved")

    def test_poll_returns_approved(self):
        def api(host, token, method, path, data=None, params=None):
            if method == "GET":
                return {"labels": [], "state": "opened"}
            return {"state": "closed"}

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list",
                               side_effect=lambda h, t, p, params=None:
                               [{"iid": 1, "state": "merged"}] if p.endswith("merge_requests") else []):
            r = gl_plugin.poll({"issue": {"host": "h", "project": "p", "iid": 5, "url": "u"},
                                "active_seen": False})
        self.assertEqual(r["decision"], "approved")

    def test_poll_undecided_returns_none(self):
        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api",
                               return_value={"labels": [], "state": "opened"}), \
             mock.patch.object(gl_plugin, "gl_api_list", return_value=[]):
            r = gl_plugin.poll({"issue": {"host": "h", "project": "p", "iid": 5}})
        self.assertIsNone(r["decision"])

    def test_poll_transient_error_returns_none(self):
        # 一過性エラー（5xx 等）は決着させず None（次回再試行）＝run を殺さない
        def api(host, token, method, path, data=None, params=None):
            raise RuntimeError("GitLab API GET ... 失敗: HTTP 500 Server Error")

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", return_value=[]):
            r = gl_plugin.poll({"issue": {"host": "h", "project": "p", "iid": 5}})
        self.assertIsNone(r["decision"])

    def test_poll_deleted_issue_rejects(self):
        def api(host, token, method, path, data=None, params=None):
            raise RuntimeError("GitLab API GET /projects/x/issues/5 失敗: HTTP 404 Not Found")

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api), \
             mock.patch.object(gl_plugin, "gl_api_list", return_value=[]):
            r = gl_plugin.poll({"issue": {"host": "h", "project": "p", "iid": 5, "url": "u"}})
        self.assertEqual(r["decision"], "rejected")
        self.assertIn("削除", r["data"]["reason"])

    def test_on_cancel_closes_issues(self):
        posts, closes = [], []

        def api(host, token, method, path, data=None, params=None):
            if method == "POST" and path.endswith("/notes"):
                posts.append(data)
            if method == "PUT":
                closes.append(data)
            return {}

        with mock.patch.object(gl_plugin, "_resolve_token", return_value="glpat-x"), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api):
            gl_plugin.on_cancel([{"issue": {"host": "h", "project": "p", "iid": 9, "url": "u"}}])
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["state_event"], "close")
        self.assertTrue(posts and posts[0]["body"].startswith("agent-flow:"))


class GitlabHumanAgentDiscriminationTests(unittest.TestCase):
    """gitlab-idd 実行を前提に、フィードバック還元へ運ぶのは**人のコメントだけ**にする判別。
    worker/reviewer エージェントのコメント（全 gitlab-idd マーカー・bot 著者・agent_authors・
    per-issue 自動学習）を除外し、人の通常コメントだけを残す。"""

    def _notes_via(self, notes, cfg=None):
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            return gl_plugin._human_notes("h", "t", "p", 1, cfg or {})

    def test_excludes_all_gitlab_idd_markers(self):
        notes = [
            {"body": "🚀 作業開始\n<!-- gitlab-idd:worker-node-id:abc -->", "system": False,
             "author": {"username": "worker1", "id": 11}},
            {"body": "scout\n<!-- gitlab-idd:scout-map -->", "system": False,
             "author": {"username": "worker1", "id": 11}},
            {"body": "承認します\n<!-- gitlab-idd:non-requester-reviewed:z -->", "system": False,
             "author": {"username": "rev1", "id": 22}},
            {"body": "命名を requirements.md に合わせて直して", "system": False,
             "author": {"username": "alice", "id": 99}},
        ]
        got = self._notes_via(notes)
        self.assertEqual([n["author"]["username"] for n in got], ["alice"])

    def test_excludes_agent_author_unmarked_comment_via_per_issue_learning(self):
        # worker1 はマーカー付き着手コメントを出す → その後のマーカー無し設計記録も同著者=エージェント扱い
        notes = [
            {"body": "🚀 着手 <!-- gitlab-idd:worker-node-id:abc -->", "system": False,
             "author": {"username": "worker1", "id": 11}},
            {"body": "📝 設計をイシューに記録しました（マーカー無し）", "system": False,
             "author": {"username": "worker1", "id": 11}},
            {"body": "実サーバで検証してください", "system": False,
             "author": {"username": "bob", "id": 5}},
        ]
        got = self._notes_via(notes)
        self.assertEqual([n["author"]["username"] for n in got], ["bob"])

    def test_excludes_bot_and_kiroflow_and_system(self):
        notes = [
            {"body": "ラベル変更", "system": True, "author": {"username": "alice"}},
            {"body": "agent-flow: 取消通知", "system": False, "author": {"username": "alice"}},
            {"body": "自動レビュー", "system": False, "author": {"username": "project_42_bot"}},
            {"body": "botフラグ", "system": False, "author": {"username": "svc", "bot": True}},
            {"body": "ここは実データに合わせて", "system": False, "author": {"username": "alice"}},
        ]
        got = self._notes_via(notes)
        self.assertEqual([n["body"] for n in got], ["ここは実データに合わせて"])

    def test_agent_authors_config_excludes(self):
        notes = [
            {"body": "自動コメント", "system": False, "author": {"username": "ci-agent", "id": 7}},
            {"body": "人の指摘", "system": False, "author": {"username": "carol", "id": 8}},
        ]
        got = self._notes_via(notes, {"agent_authors": "ci-agent, 999"})
        self.assertEqual([n["author"]["username"] for n in got], ["carol"])

    def test_human_reviewers_allowlist_keeps_only_listed(self):
        notes = [
            {"body": "許可された人", "system": False, "author": {"username": "lead", "id": 3}},
            {"body": "別の人", "system": False, "author": {"username": "random", "id": 4}},
            {"body": "著者不明", "system": False},
        ]
        got = self._notes_via(notes, {"human_reviewers": "lead"})
        self.assertEqual([n["body"] for n in got], ["許可された人"])
        # trust_unmarked_comments=true なら著者不明も拾う
        got2 = self._notes_via(notes, {"human_reviewers": "lead",
                                       "trust_unmarked_comments": True})
        self.assertEqual([n["body"] for n in got2], ["許可された人", "著者不明"])

    def test_no_allowlist_keeps_plain_human_comments(self):
        # 後方互換: allowlist 無し・著者情報が無くても、マーカー/bot でなければ人として拾う
        notes = [{"body": "命名が要件と違う", "system": False}]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            got = gl_plugin._human_comments("h", "t", "p", 1, {})
        self.assertEqual(got, "命名が要件と違う")

    def test_payload_carries_author_and_note_id(self):
        notes = [{"id": 501, "body": "実サーバで検証", "system": False,
                  "author": {"username": "dan", "id": 6}, "created_at": "2026-07-07T00:00:00Z"}]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            payload = gl_plugin._human_notes_payload("h", "t", "p", 1, {})
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["note_id"], 501)
        self.assertEqual(payload[0]["author"]["username"], "dan")
        self.assertEqual(payload[0]["body"], "実サーバで検証")

    # --- 途中の差し戻し（人コメントの見出し検知）---
    def test_rework_heading_detected(self):
        cfg = {"rework_heading": "差し戻し"}
        for body in ("# 差し戻し\n実サーバで検証してください",
                     "**差し戻し**: localhost でなく実サーバで",
                     "差し戻し"):
            notes = [{"body": body, "system": False, "author": {"username": "alice"}}]
            with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
                self.assertTrue(gl_plugin._rework_requested("h", "t", "p", 1, cfg), body)

    def test_rework_ignores_body_mention_and_disabled(self):
        # 見出しでない長い散文中のたまたまの言及では発火しない
        notes = [{"body": "これは差し戻しではなく承認ですと私は考えています。よくできています。",
                  "system": False, "author": {"username": "a"}}]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            self.assertEqual(gl_plugin._rework_requested("h", "t", "p", 1,
                             {"rework_heading": "差し戻し"}), "")
        # 空設定なら無効（機能を切れる）
        notes2 = [{"body": "# 差し戻し\n直して", "system": False, "author": {"username": "a"}}]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes2):
            self.assertEqual(gl_plugin._rework_requested("h", "t", "p", 1,
                             {"rework_heading": ""}), "")

    def test_rework_ignores_agent_heading(self):
        # エージェント（gitlab-idd マーカー）の差し戻し見出しは人でないので拾わない
        notes = [{"body": "# 差し戻し\n<!-- gitlab-idd:non-requester-reviewed:z -->",
                  "system": False, "author": {"username": "rev-bot"}}]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            self.assertEqual(gl_plugin._rework_requested("h", "t", "p", 1,
                             {"rework_heading": "差し戻し"}), "")

    def test_rework_payload_keeps_issue_open_and_consumes_note(self):
        # 途中差し戻しはイシューを閉じず、note を消費して再アタッチ即却下を防ぐ
        notes = [{"id": 77, "body": "# 差し戻し\n実サーバで", "system": False,
                  "author": {"username": "alice"}}]
        calls = []

        def api(host, token, method, path, data=None, params=None):
            calls.append((method, path, data))
            return {"ok": True}

        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes), \
             mock.patch.object(gl_plugin, "gl_api", side_effect=api):
            text, data = gl_plugin._rework_payload(
                "h", "t", "p", 1, "u1", [], {"status:open"},
                {"rework_heading": "差し戻し", "rework_label": "status:needs-rework",
                 "approved_label": "status:approved"},
                "差し戻し（人コメントの見出し）")
        self.assertFalse(data["closed"])
        self.assertTrue(data.get("rework"))
        self.assertIn("実サーバで", data["guidance"])
        self.assertIn("[gitlab-reject]", text)
        # close していない
        self.assertFalse(any(c[0] == "PUT" and "state_event" in str(c[2] or {})
                             for c in calls))
        # 消費マーカーを投稿
        note_posts = [c for c in calls if c[0] == "POST" and "/notes" in c[1]]
        self.assertEqual(len(note_posts), 1)
        self.assertIn("rework-consumed:77", note_posts[0][2]["body"])

    def test_rework_requested_skips_consumed_note(self):
        notes = [
            {"id": 77, "body": "# 差し戻し\n古い", "system": False,
             "author": {"username": "alice"}},
            {"id": 80, "body": "agent-flow: done <!-- gitlab-idd:rework-consumed:77 -->",
             "system": False, "author": {"username": "bot"}},
        ]
        with mock.patch.object(gl_plugin, "_get_comments", return_value=notes):
            self.assertEqual(gl_plugin._rework_requested("h", "t", "p", 1,
                             {"rework_heading": "差し戻し"}), "")
