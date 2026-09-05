#!/usr/bin/env python3
"""書込先の集合（workset）— agent-project 側のルーティング・検証・納品・委譲。

正典設計: docs/plans/2026-09-05-agent-flow-multi-workspace-design.md §6.3（P2）。

固定する不変条件:
  - **既定では従来どおり 1 つに決まる。** 集合になるのは人の明示 `- workspace: a, b`、
    `route: ... -> a+b`、設定 `multi_workspace: true` × owns 複数ヒットの 3 つだけ。
  - auto-route（LLM）には複数を選ばせない。
  - 書込先が 1 つのときの `--workspace` トークン・検証計画・納品エントリは 1 バイトも変わらない。
  - 書込先の集合を扱えないフリートへは板へ出さない（静かな部分実行を作らない・§5.7）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403

from agentcore import verifycontract as vc

_CHARTER = ("# Charter: r\n## goal\nx\n## repos\n"
            "- api = https://git/api.git\n  - owns: services/api/**\n  - base: main\n"
            "  - target: develop\n"
            "- web = https://git/web.git\n  - owns: apps/web/**\n  - base: main\n"
            "  - target: develop\n"
            "- docs = https://git/docs.git\n  - desc: 参照元\n  - base: main\n")


def _charter_cfg(d: Path, **kw):
    write_charter(d, _CHARTER)
    kw.setdefault("route_planner", "none")
    return cfg_for(d, **kw)


class ResolveWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        self.cfg = _charter_cfg(self.d)

    def test_single_target_still_resolves_to_one(self):
        t = km.Task(id="T1", title="x", extra=[("paths", "services/api/a.py")])
        picked, by = km.resolve_workset(self.cfg, t, km.Policy())
        self.assertEqual([s["name"] for s in picked], ["api"])
        self.assertEqual(by, "owns")
        # primary だけを見る従来の呼び出しも同じ答えを返す
        spec, by2 = km.resolve_workspace(self.cfg, t, km.Policy())
        self.assertEqual((spec["name"], by2), ("api", "owns"))

    def test_explicit_comma_list_is_a_workset(self):
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web")])
        picked, by = km.resolve_workset(self.cfg, t, km.Policy())
        self.assertEqual([s["name"] for s in picked], ["api", "web"])
        self.assertEqual(by, "explicit")

    def test_route_rule_can_name_two_targets_with_plus(self):
        pol = km.Policy(route=["横断 -> api+web"])
        t = km.Task(id="T2", title="横断の型を揃える")
        picked, by = km.resolve_workset(self.cfg, t, pol)
        self.assertEqual([s["name"] for s in picked], ["api", "web"])
        self.assertEqual(by, "rule")

    def test_route_rule_with_an_unknown_name_is_not_used(self):
        # 片方でも書込先として解決できないルールは採らない（半端な集合を作らない）。
        pol = km.Policy(route=["横断 -> api+nope"])
        t = km.Task(id="T2", title="横断の型を揃える", extra=[("paths", "services/api/a.py")])
        picked, by = km.resolve_workset(self.cfg, t, pol)
        self.assertEqual(([s["name"] for s in picked], by), (["api"], "owns"))

    def test_owns_hitting_two_repos_needs_the_opt_in(self):
        t = km.Task(id="T3", title="x",
                    extra=[("paths", "services/api/a.py, apps/web/b.ts")])
        # 既定: 決められない → 次の段（default / sole）へ落ちる。書込先候補は 2 つなので "none"
        picked, by = km.resolve_workset(self.cfg, t, km.Policy())
        self.assertEqual((picked, by), ([], "none"))
        # オプトイン: 両方に書く
        cfg = _charter_cfg(self.d, multi_workspace=True)
        picked, by = km.resolve_workset(cfg, t, km.Policy())
        self.assertEqual([s["name"] for s in picked], ["api", "web"])
        self.assertEqual(by, "owns")

    def test_auto_route_never_returns_more_than_one(self):
        cfg = _charter_cfg(self.d, route_planner="agent", multi_workspace=True)
        t = km.Task(id="T4", title="謎")
        with mock.patch.object(km, "route_agent", return_value="web"):
            picked, by = km.resolve_workset(cfg, t, km.Policy())
        self.assertEqual(([s["name"] for s in picked], by), (["web"], "agent"))

    def test_decision_is_persisted_as_one_comma_line(self):
        cfg = _charter_cfg(self.d, multi_workspace=True)
        cfg.backlog.mkdir(parents=True, exist_ok=True)
        t = km.Task(id="T5", title="x", verify="true",
                    extra=[("paths", "services/api/a.py, apps/web/b.ts")])
        km.persist_task(cfg, t)
        picked = km.resolve_and_persist_workset(cfg, t, km.Policy())
        self.assertEqual([s["name"] for s in picked], ["api", "web"])
        reloaded = km.parse_task((cfg.backlog / "T5.md").read_text(), "T5")
        self.assertEqual(reloaded.get("workspace"), "api, web")
        self.assertEqual(reloaded.get("routed_by"), "owns")


class WorkspaceArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-args-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        self.cfg = _charter_cfg(self.d, task_branch=True)

    def test_single_target_token_is_unchanged(self):
        t = km.Task(id="T1", title="x", extra=[("workspace", "api")])
        args = km._workspace_cmd_args(self.cfg, t)
        self.assertEqual(args[0], "--workspace")
        self.assertEqual(len(args), 2)
        obj = json.loads(args[1])
        self.assertNotIn("name", obj)          # 1 要素では要素名を載せない（記録の形を変えない）
        self.assertEqual(obj["branch"], "ap/T1")

    def test_two_targets_are_passed_as_two_named_arguments(self):
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web")])
        args = km._workspace_cmd_args(self.cfg, t)
        self.assertEqual(args[0::2], ["--workspace", "--workspace"])
        first, second = json.loads(args[1]), json.loads(args[3])
        self.assertEqual((first["name"], second["name"]), ("api", "web"))
        self.assertEqual((first["url"], second["url"]),
                         ("https://git/api.git", "https://git/web.git"))
        # 作業ブランチは全要素で同名（横断 MR の相関鍵）
        self.assertEqual(first["branch"], second["branch"])
        self.assertEqual(first["branch"], "ap/T1")

    def test_every_write_target_is_dropped_from_references(self):
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web"),
                                               ("refs", "api, docs")])
        refs = [s["name"] for s in km.task_reference_specs(self.cfg, t)]
        self.assertEqual(refs, ["docs"])       # 書込先はどの要素も参照に含めない

    def test_agent_flow_command_carries_every_target_and_a_v3_plan(self):
        # 投入の argv まで通して、書込先の集合と検証計画が同じ語彙で載ることを固定する。
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web"),
                                               ("verification_commands", "pytest -q")])
        cmd = km.build_agent_flow_cmd(t, self.cfg)
        names = [json.loads(cmd[i + 1])["name"]
                 for i, a in enumerate(cmd) if a == "--workspace"]
        self.assertEqual(names, ["api", "web"])
        plan = json.loads(cmd[cmd.index("--verification-plan") + 1])
        self.assertEqual(plan["version"], 3)
        self.assertEqual(plan["workspaces"], names)

    def test_element_name_falls_back_to_the_same_rule_as_agent_flow(self):
        # 名前が無い spec は URL から導く。agent-flow が clone を引く名前と一致しなければ
        # 検証計画の workspaces[] が実行場所を指せない。
        self.assertEqual(km.workset_element_name({"url": "https://git/shop.git"}), "shop")
        self.assertEqual(km.workset_element_name({"name": "api", "url": "https://x/y.git"}), "api")


class PlanWorksetTests(unittest.TestCase):
    """charter → バックログ生成（`assign_plan_workspace`）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-plan-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        write_charter(self.d, _CHARTER)
        self.charter = km.load_charter(cfg_for(self.d))

    def test_paths_in_one_repo_pick_one(self):
        sp = km.assign_plan_workspace(self.charter, {"title": "t", "paths": "services/api/a.py"})
        self.assertEqual(sp["workspace"], "api")

    def test_paths_across_repos_stay_undecided_by_default(self):
        sp = km.assign_plan_workspace(
            self.charter, {"title": "t", "paths": "services/api/a.py, apps/web/b.ts"})
        self.assertEqual(sp["workspace"], "")   # 決めない＝後段の route 層へ倒す（従来どおり）

    def test_paths_across_repos_become_a_workset_when_opted_in(self):
        sp = km.assign_plan_workspace(
            self.charter, {"title": "t", "paths": "services/api/a.py, apps/web/b.ts"},
            multi=True)
        self.assertEqual(sp["workspace"], "api, web")
        self.assertNotIn("api", str(sp.get("refs") or ""))   # 書込先は refs に落とさない
        self.assertIn("docs", str(sp.get("refs") or ""))


class VerificationPlanWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-vp-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        self.cfg = _charter_cfg(self.d, task_branch=True)
        self.cfg.backlog.mkdir(parents=True, exist_ok=True)

    def _task(self, workspace: str):
        t = km.Task(id="T1", title="x", extra=[("workspace", workspace),
                                               ("verification_commands", "pytest -q")])
        return t

    def test_single_target_plan_is_unchanged(self):
        plan = km.build_task_verification_plan(self.cfg, self._task("api"))
        self.assertEqual(plan["version"], 2)             # target 統合付き（従来どおり）
        self.assertEqual(plan["workspace"], "https://git/api.git")   # URL のまま
        self.assertNotIn("workspaces", plan)
        self.assertEqual(plan["integration"], {"target": "develop"})

    def test_two_targets_build_a_version_3_plan(self):
        plan = km.build_task_verification_plan(self.cfg, self._task("api, web"))
        self.assertEqual(plan["version"], 3)
        self.assertEqual(plan["workspaces"], ["api", "web"])
        self.assertEqual(plan["integration"], {"targets": {"api": "develop", "web": "develop"}})
        self.assertEqual(vc.plan_errors(plan), [])

    def test_plan_element_names_match_the_workspace_arguments(self):
        # runner は `--workspace` の name で clone を引く。plan の workspaces[] がそれと
        # 違う語彙だと検証場所を指せない。
        t = self._task("api, web")
        args = km._workspace_cmd_args(self.cfg, t)
        names = [json.loads(a)["name"] for a in args[1::2]]
        plan = km.build_task_verification_plan(self.cfg, t)
        self.assertEqual(plan["workspaces"], names)


class LocalReceiptWorksetTests(unittest.TestCase):
    """local runner（run が receipt を返さない経路）の workset 対応。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-lr-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        self.cfg = cfg_for(self.d)
        self.task = km.Task(id="T1", title="x")

    def _repo(self, name: str) -> str:
        root = self.d / name
        root.mkdir(parents=True)
        for cmd in (["git", "init", "-q", "-b", "main", str(root)],
                    ["git", "-C", str(root), "config", "user.email", "t@t"],
                    ["git", "-C", str(root), "config", "user.name", "t"]):
            subprocess.run(cmd, check=True, capture_output=True)
        (root / f"{name}.txt").write_text(name, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "i"], check=True,
                       capture_output=True)
        return str(root)

    def test_commands_run_in_their_element_and_see_the_other_repos(self):
        api, web = self._repo("api"), self._repo("web")
        plan = vc.build_plan("T1", workspaces=["api", "web"], commands=[
            {"command": 'test -f api.txt && test -n "$AGENT_REPO_WEB"'},
            {"command": "test -f web.txt", "cwd": "web"}])
        receipt = km.run_local_receipt(self.cfg, self.task, plan, "", Path(api), None,
                                       {"api": api, "web": web})
        self.assertEqual([c["exit_code"] for c in receipt["commands"]], [0, 0])
        self.assertEqual(sorted(receipt["revisions"]), ["api", "web"])

    def test_a_workset_plan_without_clones_is_inconclusive(self):
        plan = vc.build_plan("T1", workspaces=["api", "web"], commands=["true"])
        receipt = km.run_local_receipt(self.cfg, self.task, plan, "", self.d, None, {})
        self.assertEqual(vc.receipt_overall(receipt), "inconclusive")
        self.assertIn("検証場所が不足", receipt["commands"][0]["note"])


class ReceiptViewTests(unittest.TestCase):
    def test_integrations_are_shown_one_row_per_element(self):
        receipt = {"version": 3, "commands": [], "criteria": [],
                   "integrations": [
                       {"name": "api", "target": "develop", "target_rev": "a" * 40,
                        "verdict": "pass", "conflict_files": []},
                       {"name": "web", "target": "develop", "target_rev": "b" * 40,
                        "verdict": "fail", "conflict_files": []}]}
        view = km.receipt_to_verification(receipt)
        texts = [c["text"] for c in view["criteria"]]
        self.assertEqual(len(texts), 2)
        self.assertIn("（api）", texts[0])
        self.assertIn("（web）", texts[1])
        self.assertEqual([c["verdict"] for c in view["criteria"]], ["pass", "fail"])
        self.assertFalse(view["ok"])


class DeliveryWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-del-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        self.cfg = cfg_for(self.d, task_branch=True)

    def test_work_branches_are_listed_per_element(self):
        meta = {"workspace": {"url": "https://git/api.git", "name": "api",
                              "branch": "ap/T1", "target": "develop"},
                "workspaces": [
                    {"url": "https://git/api.git", "name": "api", "branch": "ap/T1",
                     "target": "develop"},
                    {"url": "https://git/web.git", "name": "web", "branch": "ap/T1",
                     "target": "main"}]}
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web")])
        with mock.patch.object(km, "_task_run_meta", return_value=meta):
            elements = km._task_work_branches(self.cfg, t)
            # primary だけを見る従来の呼び出しは先頭要素を返す
            self.assertEqual(km._task_work_branch(self.cfg, t), ("develop", "ap/T1"))
        self.assertEqual([(e["name"], e["target"]) for e in elements],
                         [("api", "develop"), ("web", "main")])

    def test_delivery_entries_has_one_write_row_per_element(self):
        meta = {"workspaces": [
            {"url": "https://git/api.git", "name": "api", "branch": "ap/T1", "target": "develop"},
            {"url": "https://git/web.git", "name": "web", "branch": "ap/T1", "target": "main"}]}
        t = km.Task(id="T1", title="x", extra=[("workspace", "api, web")])
        with mock.patch.object(km, "_task_run_meta", return_value=meta), \
             mock.patch.object(km, "work_branch_changes", return_value=("origin/ap/T1", ["a.py"])):
            entries = km.delivery_entries(self.cfg, t)
        writes = [e for e in entries if e["role"] == "write"]
        self.assertEqual([e["name"] for e in writes], ["api", "web"])
        self.assertEqual([e["target"] for e in writes], ["develop", "main"])


class BoardOffloadGateTests(unittest.TestCase):
    """書込先の集合を扱えないフリートへは出さない（§5.7 の fail-close）。"""

    def test_single_target_is_never_blocked(self):
        self.assertEqual(km.workset_offload_blocked([{"url": "https://git/api.git"}]), "")

    def test_two_targets_are_blocked_until_the_fleet_contract_supports_them(self):
        why = km.workset_offload_blocked([{"url": "https://git/api.git"},
                                          {"url": "https://git/web.git"}])
        self.assertIn("板へ出しません", why)

    def test_the_gate_opens_when_the_fleet_contract_is_raised(self):
        with mock.patch.object(km._boardrules, "CONTRACT_VERSION",
                               km._boardrules.WORKSET_CONTRACT_VERSION):
            self.assertEqual(km.workset_offload_blocked([{"url": "https://git/api.git"},
                                                         {"url": "https://git/web.git"}]), "")

    def test_the_envelope_carries_every_target_and_the_required_version(self):
        t = km.Task(id="T1", title="横断")
        workset = [{"name": "api", "url": "https://git/api.git", "target": "develop"},
                   {"name": "web", "url": "https://git/web.git", "target": "main"}]
        env = km.task_to_delegation(t, workset[0], workset=workset)
        self.assertEqual(env["workspace"]["url"], "https://git/api.git")   # primary は従来どおり
        self.assertEqual([w["name"] for w in env["workspaces"]], ["api", "web"])
        self.assertEqual(env["requires"]["repos"],
                         ["https://git/api.git", "https://git/web.git"])
        self.assertEqual(env["requires"]["contract_version"],
                         km._boardrules.WORKSET_CONTRACT_VERSION)

    def test_a_single_target_envelope_is_unchanged(self):
        t = km.Task(id="T1", title="ふつう")
        spec = {"name": "api", "url": "https://git/api.git", "target": "develop"}
        env = km.task_to_delegation(t, spec, workset=[spec])
        self.assertNotIn("workspaces", env)
        self.assertNotIn("requires", env)
        self.assertNotIn("name", env["workspace"])   # 1 要素では要素名を載せない


class TaskMrWorksetTests(unittest.TestCase):
    """review → MR 用意 → approve の決着を要素ごとに（P2 の完了条件の後半）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kp-ws-mr-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = Path(self.tmp)
        write_charter(self.d, "# Charter: r\n## goal\nx\n## repos\n"
                              "- api = https://gitlab.example.com/g/api.git\n"
                              "  - owns: services/api/**\n  - base: main\n  - target: develop\n"
                              "- web = https://gitlab.example.com/g/web.git\n"
                              "  - owns: apps/web/**\n  - base: main\n  - target: develop\n")
        self.cfg = cfg_for(self.d, task_branch=True, route_planner="none")
        self.cfg.backlog.mkdir(parents=True, exist_ok=True)
        self.task = km.Task(id="T1", title="横断", verify="true",
                            extra=[("workspace", "api, web")])
        km.persist_task(self.cfg, self.task)

    def _gl(self, created):
        def api(scheme, host, token, method, path, data=None, params=None):
            if method == "GET" and path.endswith("/merge_requests"):
                return []                                    # 既存 MR 無し
            if method == "POST" and path.endswith("/merge_requests"):
                proj = path.split("/projects/", 1)[1].split("/", 1)[0]
                iid = len(created) + 7
                created.append((proj, data))
                return {"iid": iid, "web_url": f"https://gitlab.example.com/mr/{iid}"}
            return {}
        return api

    def test_one_mr_per_element_with_the_same_source_branch(self):
        created: list = []
        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "forge_available", return_value="gitlab"), \
             mock.patch.object(km, "_gl_api", side_effect=self._gl(created)):
            url = km.ensure_task_mr(self.cfg, self.task)

        self.assertEqual(len(created), 2)                    # 要素ごとに 1 本
        self.assertEqual({c[1]["source_branch"] for c in created}, {"ap/T1"})
        self.assertEqual([c[1]["target_branch"] for c in created], ["develop", "develop"])
        self.assertEqual(url, "https://gitlab.example.com/mr/7")   # primary を返す（従来どおり）
        self.assertEqual(self.task.get("mr_url"), url)             # 旧い読み手はそのまま動く
        records = km._task_mr_records(self.task)
        self.assertEqual([r["name"] for r in records], ["api", "web"])

    def test_approval_needs_every_element_to_settle(self):
        created: list = []
        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "forge_available", return_value="gitlab"), \
             mock.patch.object(km, "_gl_api", side_effect=self._gl(created)):
            km.ensure_task_mr(self.cfg, self.task)

        def api(scheme, host, token, method, path, data=None, params=None):
            if method == "GET" and path.endswith("/discussions"):
                return []
            if method == "GET" and path.endswith("/changes"):
                return {"changes": [{"new_path": "a.py"}]}
            if method == "GET" and "/merge_requests/" in path:
                # 2 本目（web）だけコンフリクト＝決着していない
                iid = path.rsplit("/", 1)[-1]
                return {"state": "opened",
                        "merge_status": "cannot_be_merged" if iid == "8" else "can_be_merged",
                        "has_conflicts": iid == "8"}
            return {}

        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "_gl_api", side_effect=api):
            ok, why = km.finalize_task_mr(self.cfg, self.task)
        self.assertFalse(ok, why)             # 1 本でも決着していなければ done にしない
        self.assertIn("web", why)

    def test_every_element_merged_settles_the_task(self):
        created: list = []
        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "forge_available", return_value="gitlab"), \
             mock.patch.object(km, "_gl_api", side_effect=self._gl(created)):
            km.ensure_task_mr(self.cfg, self.task)
        merged: list = []

        def api(scheme, host, token, method, path, data=None, params=None):
            if method == "PUT" and path.endswith("/merge"):
                merged.append(path)
                return {}
            if method == "GET" and path.endswith("/discussions"):
                return []
            if method == "GET" and path.endswith("/changes"):
                return {"changes": [{"new_path": "a.py"}]}
            if method == "GET" and "/merge_requests/" in path:
                return {"state": "opened", "merge_status": "can_be_merged"}
            return {}

        with mock.patch.object(km, "_gl_token", return_value="tok"), \
             mock.patch.object(km, "_gl_api", side_effect=api):
            ok, why = km.finalize_task_mr(self.cfg, self.task)
        self.assertTrue(ok, why)
        self.assertEqual(len(merged), 2)      # 要素ごとにマージする


class ApproveAckTests(unittest.TestCase):
    """承認時の「作業ブランチが消えている」再承認の鍵（要素ごと）。"""

    def test_single_target_key_is_the_branch_itself(self):
        msg = "作業ブランチ ap/T1 を解決できないため、main へマージできません"
        self.assertEqual(km._missing_branch_acks(msg), ["ap/T1"])

    def test_multi_target_keys_are_per_element(self):
        msg = (km.UNINTEGRATED_PREFIX
               + "api: 作業ブランチ ap/T1 を解決できないため、develop へマージできません; "
                 "web: 作業ブランチ ap/T1 を解決できないため、main へマージできません")
        self.assertEqual(km._missing_branch_acks(msg), ["api|ap/T1", "web|ap/T1"])

    def test_any_other_failure_is_not_re_approvable(self):
        msg = (km.UNINTEGRATED_PREFIX
               + "api: 作業ブランチ ap/T1 を解決できないため、develop へマージできません; "
                 "web: コンフリクト（merge_status=cannot_be_merged）")
        self.assertIsNone(km._missing_branch_acks(msg))


class GateTargetTests(unittest.TestCase):
    def test_gate_targets_fall_back_to_the_single_pair(self):
        t = km.Task(id="T1", title="x",
                    extra=[("gate_target", "develop"), ("gate_target_rev", "a" * 40)])
        self.assertEqual(km._gate_targets(t),
                         [{"name": "", "target": "develop", "rev": "a" * 40}])

    def test_gate_targets_are_read_per_element_when_recorded(self):
        t = km.Task(id="T1", title="x", extra=[
            ("gate_target", "develop"), ("gate_target_rev", "a" * 40),
            ("gate_targets", json.dumps([{"name": "api", "target": "develop", "rev": "a" * 40},
                                         {"name": "web", "target": "main", "rev": "b" * 40}]))])
        self.assertEqual([g["name"] for g in km._gate_targets(t)], ["api", "web"])


if __name__ == "__main__":
    unittest.main()
