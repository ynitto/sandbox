#!/usr/bin/env python3
"""workset（1 run = 書込先の集合）のテスト。

正典設計: docs/plans/2026-09-05-agent-flow-multi-workspace-design.md

固定する不変条件:
  - 集合が 1 要素のときは従来の単一 workspace と**形も意味も変わらない**（§5.1 不変条件 3）。
  - 要素ごとに同じ規律（作業ブランチ・commit/push・publication・復旧 ref・base-sync）を適用する。
  - 片方の push が失敗しても残りの要素を finalize し、半公開を記録に残す（§5.5）。
  - gitlab executor は書込先ごとに 1 イシューを起票し、全部の承認で初めて done にする（§5.7）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403
import argparse
from unittest import mock

from agentcore import verifycontract as vc


def _make_remote(root: str, name: str, base: str = "main", subfile: str = "") -> str:
    """push 先になるローカル『リモート』（非 bare）を 1 コミットで用意する。"""
    remote = os.path.join(root, name)
    os.makedirs(remote)
    for cmd in (["git", "init", "-q", "-b", base, remote],
                ["git", "-C", remote, "config", "user.email", "t@t"],
                ["git", "-C", remote, "config", "user.name", "t"]):
        subprocess.run(cmd, check=True, capture_output=True)
    target = os.path.join(remote, subfile or "f.txt")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    pathlib.Path(target).write_text(f"seed {name}\n")   # repo ごとに revision を分ける
    subprocess.run(["git", "-C", remote, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", remote, "commit", "-qm", "init"], check=True, capture_output=True)
    return remote


def _branch_exists(remote: str, branch: str) -> bool:
    return subprocess.run(["git", "-C", remote, "rev-parse", "--verify", branch],
                          capture_output=True).returncode == 0


class NormalizeWorksetTests(unittest.TestCase):
    def test_single_unnamed_element_keeps_the_old_shape(self):
        # N=1 では name を足さない。delivery / publication へそのまま写る spec の形を
        # この変更で 1 キーも増やさないため（§5.1 不変条件 3）。
        spec = {"url": "https://x/api.git", "base": "main"}
        self.assertEqual(kf.normalize_workset([spec]), [spec])

    def test_names_are_derived_and_made_unique(self):
        out = kf.normalize_workset([{"url": "https://a/api.git"}, {"url": "https://b/api.git"}])
        self.assertEqual([e["name"] for e in out], ["api", "api-2"])

    def test_explicit_name_wins_even_for_a_single_element(self):
        out = kf.normalize_workset([{"url": "https://x/api.git", "name": "backend"}])
        self.assertEqual(out[0]["name"], "backend")

    def test_identical_elements_collapse(self):
        spec = {"url": "https://x/api.git", "path": "", "base": "main"}
        self.assertEqual(len(kf.normalize_workset([spec, dict(spec)])), 1)

    def test_url_without_scheme_is_dropped(self):
        self.assertEqual(kf.normalize_workset([{"url": ""}, None, "x"]), [])

    def test_same_url_with_different_base_is_rejected(self):
        # 同名の作業ブランチ af/<run-id> の起点が矛盾する（§5.1）。
        errs = kf.workset_errors(kf.normalize_workset([
            {"url": "https://x/api.git", "base": "main"},
            {"url": "https://x/api.git", "base": "develop", "path": "sub"}]))
        self.assertTrue(errs)
        self.assertIn("api.git", errs[0])

    def test_same_url_different_base_is_allowed_with_explicit_branches(self):
        self.assertEqual(kf.workset_errors(kf.normalize_workset([
            {"url": "https://x/api.git", "base": "main", "branch": "ap/1"},
            {"url": "https://x/api.git", "base": "develop", "branch": "ap/2"}])), [])

    def test_parse_workset_accepts_repeats_and_a_json_array(self):
        repeated = kf.parse_workset(['{"url": "https://x/api.git", "name": "api"}',
                                     "https://x/web.git"])
        self.assertEqual([e["name"] for e in repeated], ["api", "web"])
        array = kf.parse_workset(['[{"url": "https://x/api.git", "name": "api"},'
                                  ' {"url": "https://x/web.git", "name": "web"}]'])
        self.assertEqual([e["name"] for e in array], ["api", "web"])

    def test_references_overlapping_the_workset_are_dropped(self):
        workset = kf.normalize_workset([{"url": "https://x/api.git"}, {"url": "https://x/web.git"}])
        refs = [{"url": "https://x/api.git"}, {"url": "https://x/docs.git"}]
        self.assertEqual(kf.drop_workset_references(refs, workset),
                         [{"url": "https://x/docs.git"}])

    def test_workset_path_resolves_the_name_prefix(self):
        workset = [{"name": "api", "clone": "/tmp/api"}, {"name": "web", "clone": "/tmp/web"}]
        self.assertEqual(kf.workset_path(workset, "web:src/index.ts"), "/tmp/web/src/index.ts")
        self.assertEqual(kf.workset_path(workset, "src/app.py"), "src/app.py")   # primary 相対
        self.assertEqual(kf.workset_path(workset, "nope:x"), "nope:x")           # 未知は素通し


class InstructionTests(unittest.TestCase):
    def test_single_element_keeps_the_sole_write_target_wording(self):
        ws = {"url": "https://x/api.git", "clone": "/tmp/api", "branch": "af/r1"}
        self.assertEqual(kf.workset_instruction([ws]), kf.workspace_instruction(ws))
        self.assertIn("唯一の書込先", kf.workset_instruction([ws]))

    def test_multiple_elements_list_every_target_and_forbid_the_rest(self):
        text = kf.workset_instruction([
            {"name": "api", "url": "https://x/api.git", "clone": "/tmp/api", "branch": "af/r1"},
            {"name": "web", "url": "https://x/web.git", "clone": "/tmp/web", "branch": "af/r1",
             "path": "apps/web"}])
        self.assertNotIn("唯一の書込先", text)
        self.assertIn("/tmp/api", text)
        self.assertIn("/tmp/web", text)
        self.assertIn("apps/web 配下のみ", text)
        self.assertIn("列挙したディレクトリ以外は変更しない", text)


class BaseSyncInjectionTests(unittest.TestCase):
    def test_single_element_keeps_the_fixed_node_id(self):
        nodes = {"root": {"goal": "g", "deps": [], "kind": "work"}}
        injected = kf.inject_base_syncs(nodes, [
            {"url": "https://x/api.git", "branch": "ap/1", "target": "develop"}])
        self.assertEqual([t["id"] for t in injected], ["base-sync"])
        self.assertEqual(nodes["root"]["deps"], ["base-sync"])

    def test_one_node_per_element_and_roots_depend_on_all_of_them(self):
        nodes = {"root": {"goal": "g", "deps": [], "kind": "work"}}
        injected = kf.inject_base_syncs(nodes, [
            {"name": "api", "url": "https://x/api.git", "branch": "ap/1", "target": "develop"},
            {"name": "web", "url": "https://x/web.git", "branch": "ap/1", "target": "main"}])
        self.assertEqual([t["id"] for t in injected], ["base-sync@api", "base-sync@web"])
        self.assertEqual(nodes["root"]["deps"], ["base-sync@api", "base-sync@web"])
        # 要素ごとの base-sync は自分の要素だけを同期する（§5.6 の絞り込みを使う）。
        self.assertEqual(nodes["base-sync@api"]["workspaces"], ["api"])

    def test_node_ids_stay_filesystem_safe(self):
        # ノード id は tasks/<id>.json と claims/<id>/ のパスになる。`:` は Windows で不正。
        nodes = {}
        injected = kf.inject_base_syncs(nodes, [
            {"name": "a", "url": "https://x/a.git", "branch": "ap/1", "target": "m"},
            {"name": "b", "url": "https://x/b.git", "branch": "ap/1", "target": "m"}])
        for task in injected:
            self.assertNotIn(":", task["id"])

    def test_elements_without_an_explicit_branch_are_not_synced(self):
        # base-sync は明示作業ブランチ（agent-project のタスク単位ブランチ等）だけの仕組み。
        self.assertEqual(kf.inject_base_syncs({}, [
            {"name": "api", "url": "https://x/api.git", "target": "develop"}]), [])


class NodeWorksetTests(unittest.TestCase):
    def setUp(self):
        self.workset = [{"name": "api"}, {"name": "web"}]

    def test_default_is_every_element(self):
        self.assertEqual(kf.node_workset(self.workset, {"goal": "g"}), self.workset)

    def test_declared_narrowing_picks_only_those_elements(self):
        self.assertEqual(kf.node_workset(self.workset, {"workspaces": ["web"]}),
                         [{"name": "web"}])

    def test_narrowing_to_nothing_known_falls_back_to_every_element(self):
        # 綴り間違いで「書込先ゼロ」の静かな読み取り専用実行にしない。
        self.assertEqual(kf.node_workset(self.workset, {"workspaces": ["typo"]}), self.workset)


class DeliveryRecordTests(unittest.TestCase):
    def test_single_element_records_a_delivery_exactly_like_before(self):
        record = {"url": "u", "branch": "af/r1", "commit": "c",
                  "publication": {"state": "published"}}
        self.assertEqual(kf.merge_delivery_record({"ok": True}, [record], multi=False),
                         {"ok": True, "delivery": record})

    def test_single_element_without_changes_records_only_not_required(self):
        record = {"url": "u", "branch": "af/r1",
                  "publication": {"state": "not-required", "url": "u", "branch": "af/r1"}}
        out = kf.merge_delivery_record(None, [record], multi=False)
        self.assertEqual(out, {"publication": record["publication"]})
        self.assertNotIn("delivery", out)
        self.assertNotIn("deliveries", out)

    def test_multiple_elements_record_deliveries_and_an_aggregate(self):
        out = kf.merge_delivery_record(None, [
            {"name": "api", "publication": {"state": "published"}},
            {"name": "web", "publication": {"state": "not-required"}}], multi=True)
        self.assertEqual([d["name"] for d in out["deliveries"]], ["api", "web"])
        self.assertEqual(out["publication"]["state"], "published")
        self.assertEqual(out["publication"]["repositories"], ["api"])
        self.assertEqual(out["delivery"]["name"], "api")   # primary の成果は従来の器にも残す

    def test_aggregate_takes_the_worst_state(self):
        agg = kf.aggregate_publication([
            {"name": "api", "publication": {"state": "published"}},
            {"name": "web", "publication": {"state": "failed"}}])
        self.assertEqual(agg["state"], "failed")
        self.assertEqual(agg["repositories"], ["api"])
        self.assertEqual(agg["failed"], ["web"])


class WorksetGitTests(unittest.TestCase):
    """実際の git リポジトリを 2 つ用意して、要素ごとの clone / commit / push を確かめる。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-workset-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(kf.cleanup_workspace)
        self.api = _make_remote(self.tmp, "api_remote")
        self.web = _make_remote(self.tmp, "web_remote")
        self.workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "target": "main", "name": "api"},
            {"url": self.web, "base": "main", "target": "main", "name": "web"}])

    def _ready(self, run_id="run-ws"):
        return kf.ensure_workset(self.workset, run_id)

    def _edit(self, specs, body="change"):
        for spec in specs:
            pathlib.Path(spec["clone"], "new.txt").write_text(body)

    def test_every_element_is_cloned_with_the_same_run_branch(self):
        ready = self._ready()
        self.assertEqual([e["name"] for e in ready], ["api", "web"])
        self.assertEqual({e["branch"] for e in ready}, {"af/run-ws"})
        self.assertNotEqual(ready[0]["clone"], ready[1]["clone"])
        for spec in ready:
            self.assertTrue(os.path.exists(os.path.join(spec["clone"], ".git")))

    def test_finalize_pushes_every_changed_element(self):
        ready = self._ready()
        self._edit(ready)
        deliveries = kf.finalize_workset(ready, "run-ws", "t1")

        self.assertEqual([d["name"] for d in deliveries], ["api", "web"])
        for d in deliveries:
            self.assertEqual(d["publication"]["state"], "published")
            self.assertEqual(d["publication"]["name"], d["name"])
        self.assertTrue(_branch_exists(self.api, "af/run-ws"))
        self.assertTrue(_branch_exists(self.web, "af/run-ws"))
        self.assertEqual(kf.aggregate_publication(deliveries)["repositories"], ["api", "web"])

    def test_unchanged_elements_are_recorded_as_not_required(self):
        ready = self._ready()
        pathlib.Path(ready[0]["clone"], "only-api.txt").write_text("x")
        deliveries = kf.finalize_workset(ready, "run-ws", "t1")

        self.assertEqual(deliveries[0]["publication"]["state"], "published")
        self.assertEqual(deliveries[1]["publication"]["state"], "not-required")
        self.assertFalse(_branch_exists(self.web, "af/run-ws"))   # 触っていない repo は push しない

    def test_a_failing_push_leaves_the_other_element_published(self):
        """半公開（§5.5）: 片方が published のまま残り、集約は failed になる。"""
        ready = self._ready()
        self._edit(ready)
        shutil.rmtree(self.web)                       # web への push だけを不能にする

        with self.assertRaises(kf.WorkspacePublishError) as caught:
            kf.finalize_workset(ready, "run-ws", "t1")

        deliveries = caught.exception.data["deliveries"]
        self.assertEqual(deliveries[0]["publication"]["state"], "published")
        self.assertEqual(deliveries[1]["publication"]["state"], "failed")
        self.assertEqual(deliveries[1]["publication"]["name"], "web")
        self.assertEqual(caught.exception.data["publication"]["state"], "failed")
        self.assertEqual(caught.exception.data["publication"]["repositories"], ["api"])
        self.assertEqual(caught.exception.data["publication"]["failed"], ["web"])
        self.assertTrue(_branch_exists(self.api, "af/run-ws"),
                        "先に成功した要素の push は取り消さない（原子的にはできない）")
        self.assertEqual(caught.exception.data["error_class"], "workspace_publish")

    def test_resume_only_republishes_the_element_that_failed(self):
        ready = self._ready()
        self._edit(ready)
        moved = self.web + ".away"
        shutil.move(self.web, moved)
        with self.assertRaises(kf.WorkspacePublishError):
            kf.finalize_workset(ready, "run-ws", "t1")

        shutil.move(moved, self.web)                  # 障害が解けた → resume 相当
        kf.cleanup_workspace()                        # 作業ツリーは作り直す（park / 再 claim と同じ）
        again = self._ready()
        self._edit(again)                             # 同じ成果を再生成する
        deliveries = kf.finalize_workset(again, "run-ws", "t1")

        self.assertEqual(deliveries[0]["publication"]["state"], "not-required",
                         "既に remote にある要素は差分ゼロ＝再 push しない")
        self.assertEqual(deliveries[1]["publication"]["state"], "published")
        self.assertTrue(_branch_exists(self.web, "af/run-ws"))

    def test_a_failed_element_keeps_a_recovery_ref_in_its_own_local(self):
        local = os.path.join(self.tmp, "web_local")
        subprocess.run(["git", "clone", "-q", self.web, local], check=True, capture_output=True)
        workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "name": "api"},
            {"url": self.web, "base": "main", "name": "web", "local": local}])
        ready = kf.ensure_workset(workset, "run-rec")
        self._edit(ready)
        shutil.rmtree(self.web)

        with self.assertRaises(kf.WorkspacePublishError) as caught:
            kf.finalize_workset(ready, "run-rec", "t1")

        recovery = caught.exception.data["deliveries"][1]["publication"]["recovery"]
        self.assertEqual(recovery["repository"], local)
        self.assertEqual(subprocess.run(
            ["git", "-C", local, "rev-parse", "--verify", recovery["ref"]],
            capture_output=True).returncode, 0)

    def test_changes_outside_the_declared_path_are_refused(self):
        workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "name": "api"},
            {"url": self.web, "base": "main", "name": "web", "path": "apps/web"}])
        ready = kf.ensure_workset(workset, "run-scope")
        pathlib.Path(ready[1]["clone"], "elsewhere.txt").write_text("x")

        with self.assertRaisesRegex(RuntimeError, "許可された範囲の外"):
            kf.finalize_workset(ready, "run-scope", "t1")

    def test_changes_inside_the_declared_path_are_allowed(self):
        workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "name": "api"},
            {"url": self.web, "base": "main", "name": "web", "path": "apps/web"}])
        ready = kf.ensure_workset(workset, "run-scope2")
        target = pathlib.Path(ready[1]["clone"], "apps", "web", "a.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

        deliveries = kf.finalize_workset(ready, "run-scope2", "t1")
        self.assertEqual(deliveries[1]["publication"]["state"], "published")

    def test_single_element_finalize_is_unchanged(self):
        # N=1 は従来どおり: name も deliveries も付かない delivery 1 件。
        ready = kf.ensure_workset(kf.normalize_workset(
            [{"url": self.api, "base": "main", "target": "main"}]), "run-one")
        self._edit(ready)
        deliveries = kf.finalize_workset(ready, "run-one", "t1")
        self.assertEqual(len(deliveries), 1)
        self.assertNotIn("name", deliveries[0])
        self.assertNotIn("name", deliveries[0]["publication"])
        self.assertEqual(deliveries[0]["publication"]["state"], "published")

    def test_single_element_publish_failure_raises_the_original_error(self):
        ready = kf.ensure_workset(kf.normalize_workset(
            [{"url": self.api, "base": "main"}]), "run-one-fail")
        self._edit(ready)
        shutil.rmtree(self.api)
        with self.assertRaises(kf.WorkspacePublishError) as caught:
            kf.finalize_workset(ready, "run-one-fail", "t1")
        self.assertNotIn("deliveries", caught.exception.data)   # 形は 1 要素のまま

    def test_same_repo_two_paths_share_one_clone_and_one_commit(self):
        workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "name": "front", "path": "apps/front"},
            {"url": self.api, "base": "main", "name": "back", "path": "apps/back"}])
        self.assertEqual(kf.workset_errors(workset), [])
        ready = kf.ensure_workset(workset, "run-share")
        self.assertEqual(ready[0]["clone"], ready[1]["clone"])
        for name in ("front", "back"):
            target = pathlib.Path(ready[0]["clone"], "apps", name, "a.txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")

        deliveries = kf.finalize_workset(ready, "run-share", "t1")
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(deliveries[0]["commit"], deliveries[1]["commit"])   # push は 1 回


class BusWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-bus-ws-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-1")

    def test_single_element_meta_has_no_workspaces_key(self):
        self.bus.ensure_run("req", {"url": "https://x/api.git", "base": "main"})
        meta = kf.read_json(self.bus.meta_path)
        self.assertNotIn("workspaces", meta)
        self.assertEqual(self.bus.run_workspace()["url"], "https://x/api.git")
        self.assertEqual(len(self.bus.run_workset()), 1)

    def test_multi_element_meta_keeps_the_primary_for_old_readers(self):
        workset = kf.normalize_workset([{"url": "https://x/api.git", "name": "api"},
                                        {"url": "https://x/web.git", "name": "web"}])
        self.bus.ensure_run("req", workset[0], workspaces=workset)
        meta = kf.read_json(self.bus.meta_path)
        self.assertEqual(meta["workspace"]["url"], "https://x/api.git")
        self.assertEqual([e["name"] for e in meta["workspaces"]], ["api", "web"])
        self.assertEqual(self.bus.run_workspace()["name"], "api")
        self.assertEqual(kf.workset_names(self.bus.run_workset()), ["api", "web"])

    def test_old_runs_without_workspaces_read_as_a_single_element_workset(self):
        self.bus.ensure_run("req", {"url": "https://x/api.git"})
        self.assertEqual(len(self.bus.run_workset()), 1)

    def test_submit_request_rejects_a_primary_that_disagrees(self):
        with self.assertRaisesRegex(ValueError, "食い違"):
            self.bus.submit_request(
                "run-2", "req", "me", workspace={"url": "https://x/web.git"},
                workspaces=[{"url": "https://x/api.git"}, {"url": "https://x/web.git"}])

    def test_submit_request_rejects_an_invalid_workset(self):
        with self.assertRaisesRegex(ValueError, "workset が不正"):
            self.bus.submit_request(
                "run-3", "req", "me",
                workspaces=[{"url": "https://x/api.git", "base": "main"},
                            {"url": "https://x/api.git", "base": "dev", "path": "s"}])

    def test_generation_handover_rebases_every_element_on_the_old_branch(self):
        workset = kf.normalize_workset([{"url": "https://x/api.git", "name": "api"},
                                        {"url": "https://x/web.git", "name": "web"}])
        old = kf.Bus(os.path.join(self.tmp, "bus"), "run-old")
        old.ensure_run("req", workset[0], workspaces=workset)
        new = kf.Bus(os.path.join(self.tmp, "bus"), "run-new")
        new._seed_from(old)
        meta = kf.read_json(new.meta_path)
        self.assertEqual([e["base"] for e in meta["workspaces"]],
                         ["af/run-old", "af/run-old"])
        self.assertEqual(meta["workspace"]["base"], "af/run-old")


class VerifyPlanWorksetTests(unittest.TestCase):
    """検証計画 version 3（要素ごとの実行場所と revision）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-vp-ws-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(kf.cleanup_workspace)
        self.api = _make_remote(self.tmp, "api_remote")
        self.web = _make_remote(self.tmp, "web_remote")
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-vp")
        self.args = argparse.Namespace(run_id="run-vp", node_id="orch", model=None, request="req")
        # 検証 runner は成果ブランチを fetch して最新へ進める。ここでは push 済みの成果を
        # 模して、明示ブランチ（既に remote にある main）を成果ブランチとして使う。
        self.workset = kf.normalize_workset([
            {"url": self.api, "base": "main", "branch": "main", "name": "api"},
            {"url": self.web, "base": "main", "branch": "main", "name": "web"}])

    def _seed(self, plan):
        self.bus.ensure_run("req", self.workset[0], [], plan, workspaces=self.workset)

    def test_v2_plan_on_a_multi_target_run_is_inconclusive(self):
        # 検証場所が 1 つしかない plan で「もう片方は見ていない pass」を作らない（§5.4）。
        self._seed(vc.build_plan("T-1", commands=["true"], workspace="api",
                                 integration={"target": "main"}))
        receipt = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(receipt["verdict"], "inconclusive")
        self.assertTrue(receipt["commands"][0]["inconclusive"])
        self.assertIn("検証場所が不足", receipt["commands"][0]["note"])

    def test_v3_plan_runs_each_command_in_its_own_element(self):
        plan = vc.build_plan("T-1", workspaces=["api", "web"], commands=[
            {"command": "test -f f.txt && test -n \"$AGENT_REPO_WEB\""},
            {"command": "test -f f.txt", "cwd": "web"}])
        self.assertEqual(plan["version"], 3)
        self._seed(plan)
        receipt = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(receipt["verdict"], "pass", receipt)
        self.assertEqual(receipt["workspaces"], ["api", "web"])
        self.assertEqual(sorted(receipt["revisions"]), ["api", "web"])
        self.assertNotEqual(receipt["revisions"]["api"], receipt["revisions"]["web"])
        self.assertEqual(receipt["result_rev"], receipt["revisions"]["api"])   # primary
        self.assertEqual(vc.receipt_errors(receipt, plan=plan), [])

    def test_v3_command_cwd_actually_changes_the_directory(self):
        pathlib.Path(self.web, "web-only.txt").write_text("x")
        subprocess.run(["git", "-C", self.web, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.web, "commit", "-qm", "web"], check=True,
                       capture_output=True)
        plan = vc.build_plan("T-2", workspaces=["api", "web"], commands=[
            {"command": "test -f web-only.txt", "cwd": "web"},
            {"command": "test ! -f web-only.txt"}])
        self._seed(plan)
        receipt = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual(receipt["verdict"], "pass", receipt)

    def test_v3_integration_is_reported_per_element(self):
        plan = vc.build_plan("T-3", workspaces=["api", "web"], commands=["true"],
                             integration={"targets": {"api": "main", "web": "main"}})
        self._seed(plan)
        receipt = kf.run_verification_plan(self.bus, self.args, "orch")
        self.assertEqual([r["name"] for r in receipt["integrations"]], ["api", "web"])
        self.assertEqual({r["verdict"] for r in receipt["integrations"]}, {"pass"})
        self.assertEqual(receipt["verdict"], "pass", receipt)

    def test_one_element_missing_the_target_fails_the_whole_receipt(self):
        receipt = {"version": 3, "commands": [], "criteria": [{"id": "C1", "verdict": "pass",
                                                               "evidence": [{"kind": "command"}]}],
                   "integrations": [
                       {"name": "api", "target": "main", "target_rev": "a" * 40,
                        "verdict": "pass", "conflict_files": []},
                       {"name": "web", "target": "main", "target_rev": "b" * 40,
                        "verdict": "fail", "conflict_files": []}]}
        self.assertEqual(vc.receipt_overall(receipt), "fail")

    def test_fix_task_re_syncs_only_the_elements_that_are_behind(self):
        task = kf.verify_fix_task({"integrations": [
            {"name": "api", "target": "develop", "verdict": "pass"},
            {"name": "web", "target": "main", "verdict": "fail"}]}, 1)
        self.assertEqual(task["kind"], "base-sync")
        self.assertEqual(task["workspaces"], ["web"])


class GitlabExecutorWorksetTests(unittest.TestCase):
    """書込先ごとに 1 イシューを起票し、全部の承認で初めて done にする（§5.7・P4）。"""

    def _module(self):
        path = pathlib.Path(__file__).resolve().parent.parent / "executors" / "gitlab.py"
        spec = importlib.util.spec_from_file_location("kf_gitlab_executor", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _elements(self):
        return [{"name": "api", "url": "https://x/api.git", "base": "main"},
                {"name": "web", "url": "https://x/web.git", "base": "trunk"}]

    def test_doctor_no_longer_refuses_a_workset(self):
        args = argparse.Namespace(
            executor="gitlab", workspace=['{"url": "https://x/api.git", "name": "api"}',
                                          '{"url": "https://x/web.git", "name": "web"}'])
        findings = kf.workset_capability_findings(args)
        titles = [f["title"] for f in findings]
        self.assertIn("gitlab executor は書込先ごとに 1 イシューを起票する", titles)
        self.assertEqual([f["severity"] for f in findings], ["info"])

    def test_no_finding_for_a_single_write_target(self):
        args = argparse.Namespace(executor="gitlab", workspace=["https://x/api.git"])
        self.assertEqual(kf.workset_capability_findings(args), [])

    def test_element_tokens_differ_so_reattach_is_unambiguous(self):
        m = self._module()
        single = m._element_token("kf-abc", "api", multi=False)
        self.assertEqual(single, "kf-abc")          # N=1 は従来のトークンのまま
        a = m._element_token("kf-abc", "api", multi=True)
        b = m._element_token("kf-abc", "web", multi=True)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("kf-abc-"))

    def test_single_element_workset_is_not_treated_as_a_set(self):
        m = self._module()
        ws = {"url": "https://x/api.git"}
        self.assertEqual(m._workset_elements(ws, [{"url": "https://x/api.git"}]), [ws])

    def test_one_issue_per_element_with_its_own_project_and_target(self):
        m = self._module()
        created = []

        def fake_create(host, token, project, title, body, labels):
            created.append({"project": project, "title": title, "body": body})
            return {"iid": 10 + len(created), "web_url": f"https://x/{project}/-/issues/1"}

        with mock.patch.object(m, "_resolve_token", return_value="t"), \
                mock.patch.object(m, "_find_open_issue_by_token", return_value=None), \
                mock.patch.object(m, "_create_issue", side_effect=fake_create), \
                mock.patch.object(m, "_check_workset_decision",
                                  return_value={"decision": None, "text": None, "data": None,
                                                "active_seen": False, "mrs": 0}) as checked, \
                mock.patch.dict(os.environ, {"AGENT_FLOW_DEFER_WAITS": "1"}, clear=False):
            with self.assertRaises(m.DeferDecision) as caught:
                m.execute("work", "g", {}, art_dir="/b/runs/r1/artifacts/t1",
                          workspace=self._elements()[0], workset=self._elements())
        self.assertEqual([c["project"] for c in created], ["api", "web"])
        # どの書込先のイシューかがタイトルだけで分かる（レビュアーは 2 件を並べて見る）
        self.assertTrue(created[0]["title"].startswith("[agent-flow][api] "))
        self.assertNotEqual(created[0]["body"], created[1]["body"])  # 要素ごとのトークン
        defer = caught.exception.defer
        self.assertEqual([i["name"] for i in defer["issues"]], ["api", "web"])
        self.assertEqual(defer["expected_targets"], {"api": "main", "web": "trunk"})
        # 単数形も primary で埋まる（park 記録を 1 件しか見ない道具のため）
        self.assertEqual(defer["issue"]["name"], "api")
        self.assertEqual(defer["expected_target"], "main")
        self.assertEqual(checked.call_count, 1)

    def test_a_single_element_still_parks_with_the_old_singular_record(self):
        m = self._module()
        with mock.patch.object(m, "_resolve_token", return_value="t"), \
                mock.patch.object(m, "_find_open_issue_by_token", return_value=None), \
                mock.patch.object(m, "_create_issue",
                                  return_value={"iid": 7, "web_url": "u"}), \
                mock.patch.object(m, "_check_decision",
                                  return_value={"decision": None, "text": None, "data": None,
                                                "active_seen": False, "mrs": 0}), \
                mock.patch.dict(os.environ, {"AGENT_FLOW_DEFER_WAITS": "1"}, clear=False):
            with self.assertRaises(m.DeferDecision) as caught:
                m.execute("work", "g", {}, art_dir="/b/runs/r1/artifacts/t1",
                          workspace={"url": "https://x/api.git", "base": "main"})
        defer = caught.exception.defer
        self.assertEqual(defer["issue"]["iid"], 7)
        self.assertNotIn("issues", defer)            # 複数形のキーは足さない
        self.assertNotIn("expected_targets", defer)


class GitlabWorksetDecisionTests(unittest.TestCase):
    """要素ごとの決着を AND で畳む。"""

    def _module(self):
        path = pathlib.Path(__file__).resolve().parent.parent / "executors" / "gitlab.py"
        spec = importlib.util.spec_from_file_location("kf_gitlab_executor", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _issues(self):
        return [{"host": "h", "project": "x/api", "iid": 1, "url": "u1", "name": "api"},
                {"host": "h", "project": "x/web", "iid": 2, "url": "u2", "name": "web"}]

    def _decision(self, per_project: dict):
        def fake(host, token, project, iid, url, cfg, active_seen, expected_target=""):
            return per_project[project]
        return fake

    def test_all_approved_is_approved_and_keeps_every_element(self):
        m = self._module()
        ok = lambda name: {"decision": "approved", "text": f"{name} ok", "data": {"n": name},
                           "active_seen": True, "mrs": 1}
        with mock.patch.object(m, "_check_decision",
                               side_effect=self._decision({"x/api": ok("api"), "x/web": ok("web")})):
            r = m._check_workset_decision(self._issues(), "t", {}, False,
                                          {"api": "main", "web": "trunk"})
        self.assertEqual(r["decision"], "approved")
        self.assertEqual([e["name"] for e in r["data"]["elements"]], ["api", "web"])
        self.assertIn("## api", r["text"])
        self.assertIn("## web", r["text"])

    def test_one_rejection_rejects_the_node(self):
        m = self._module()
        with mock.patch.object(m, "_check_decision", side_effect=self._decision({
                "x/api": {"decision": "approved", "text": "a", "data": {},
                          "active_seen": True, "mrs": 1},
                "x/web": {"decision": "rejected", "text": "だめ", "data": {"decision": "rejected"},
                          "active_seen": True, "mrs": 1}})):
            r = m._check_workset_decision(self._issues(), "t", {}, False, {})
        self.assertEqual(r["decision"], "rejected")
        self.assertEqual(r["data"]["element"], "web")
        self.assertIn("[web]", r["text"])

    def test_partial_approval_keeps_waiting(self):
        m = self._module()
        with mock.patch.object(m, "_check_decision", side_effect=self._decision({
                "x/api": {"decision": "approved", "text": "a", "data": {},
                          "active_seen": True, "mrs": 1},
                "x/web": {"decision": None, "text": None, "data": None,
                          "active_seen": False, "mrs": 0}})):
            r = m._check_workset_decision(self._issues(), "t", {}, False, {})
        self.assertIsNone(r["decision"])
        self.assertTrue(r["active_seen"])            # 片方が動いていれば猶予を延ばす

    def test_a_transient_failure_on_one_element_does_not_kill_the_run(self):
        m = self._module()
        def flaky(host, token, project, iid, url, cfg, active_seen, expected_target=""):
            if project == "x/web":
                raise RuntimeError("HTTP 502")
            return {"decision": "approved", "text": "a", "data": {}, "active_seen": True, "mrs": 1}
        with mock.patch.object(m, "_check_decision", side_effect=flaky):
            r = m._check_workset_decision(self._issues(), "t", {}, False, {})
        self.assertIsNone(r["decision"])             # 却下でも承認でもなく次巡へ

    def test_poll_folds_a_workset_park_record(self):
        m = self._module()
        with mock.patch.object(m, "_resolve_token", return_value="t"), \
                mock.patch.object(m, "_check_workset_decision",
                                  return_value={"decision": "approved", "text": "t",
                                                "data": {"d": 1}, "active_seen": True,
                                                "mrs": 0}) as folded:
            r = m.poll({"issues": self._issues(), "expected_targets": {"api": "main"}})
        self.assertEqual(r["decision"], "approved")
        self.assertEqual(folded.call_args[0][4], {"api": "main"})

    def test_cancel_closes_every_element_issue(self):
        m = self._module()
        closed = []
        with mock.patch.object(m, "_resolve_token", return_value="t"), \
                mock.patch.object(m, "_add_note"), \
                mock.patch.object(m, "_close_issue",
                                  side_effect=lambda h, t, p, i: closed.append(i)):
            m.on_cancel([{"issues": self._issues()}])
        self.assertEqual(closed, [1, 2])


class GitlabWorksetWaitRecordTests(unittest.TestCase):
    """park 記録は要素ごとのイシューを持ち、単数形の run では形が変わらない。"""

    def test_workset_defer_carries_the_plural_keys(self):
        rec = kf.build_wait_record("t1", "w", "work", {
            "executor": "gitlab", "issue": {"iid": 1, "name": "api"},
            "issues": [{"iid": 1, "name": "api"}, {"iid": 2, "name": "web"}],
            "expected_target": "main", "expected_targets": {"api": "main", "web": "trunk"},
        }, 60.0)
        self.assertEqual([i["iid"] for i in rec["issues"]], [1, 2])
        self.assertEqual(rec["expected_targets"], {"api": "main", "web": "trunk"})
        self.assertEqual(rec["issue"]["iid"], 1)

    def test_single_defer_keeps_the_old_record_shape(self):
        rec = kf.build_wait_record("t1", "w", "work", {
            "executor": "gitlab", "issue": {"iid": 1}, "expected_target": "main"}, 60.0)
        self.assertNotIn("issues", rec)
        self.assertNotIn("expected_targets", rec)


class BoardResultWorksetTests(unittest.TestCase):
    """板の result.json は要素ごとの成果を載せる（P4・board.schema.json §result）。"""

    def _extras(self, nodes, results):
        return kf._board_deliveries(nodes, results)

    def test_each_element_carries_its_branch_and_commit(self):
        nodes = {"t1": {}}
        results = {"t1": {"finished_at": "2026-09-05T00:00:00Z", "data": {"deliveries": [
            {"name": "api", "publication": {"state": "published", "url": "u1",
                                            "branch": "af/x", "commit": "a" * 40}},
            {"name": "web", "publication": {"state": "not-required", "url": "u2"}}]}}}
        self.assertEqual(self._extras(nodes, results), [
            {"name": "api", "url": "u1", "branch": "af/x", "commit": "a" * 40},
            {"name": "web", "url": "u2"}])   # 変更なしの要素は commit を持たない

    def test_a_single_element_run_writes_no_deliveries(self):
        nodes = {"t1": {}}
        results = {"t1": {"data": {"deliveries": [
            {"name": "api", "publication": {"state": "published", "branch": "af/x"}}]}}}
        self.assertEqual(self._extras(nodes, results), [])

    def test_the_last_published_node_wins_per_element(self):
        # 同じ作業ブランチへ積み増すので、要素の成果は最後の commit。グラフの定義順ではなく
        # finished_at で決める（定義順は実行順と一致しない）。
        nodes = {"late": {}, "early": {}}
        results = {
            "late": {"finished_at": "2026-09-05T02:00:00Z", "data": {"deliveries": [
                {"name": "api", "publication": {"branch": "af/x", "commit": "b" * 40}},
                {"name": "web", "publication": {"branch": "af/x"}}]}},
            "early": {"finished_at": "2026-09-05T01:00:00Z", "data": {"deliveries": [
                {"name": "api", "publication": {"branch": "af/x", "commit": "a" * 40}},
                {"name": "web", "publication": {"branch": "af/x"}}]}},
        }
        got = self._extras(nodes, results)
        self.assertEqual(got[0]["commit"], "b" * 40)
        self.assertEqual([e["name"] for e in got], ["api", "web"])


class CiWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-ci-ws-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-ci")
        self.bus.ensure_dirs()
        self.bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}}})

    def test_ci_is_collected_per_element_and_the_worst_state_wins(self):
        kf.write_json_atomic(self.bus.result_path("t1"), {
            "status": "done", "data": {"deliveries": [
                {"name": "api", "publication": {"state": "published", "url": "u1",
                                                "branch": "af/x", "commit": "a" * 40}},
                {"name": "web", "publication": {"state": "published", "url": "u2",
                                                "branch": "af/x", "commit": "b" * 40}}],
                "publication": {"state": "published"}}})
        reports = {"a" * 40: {"state": "passed"}, "b" * 40: {"state": "failed"}}
        args = argparse.Namespace(ci_status_command="true", ci_wait_seconds=0, ci_poll_seconds=1)

        overall = kf.attach_ci_results(
            self.bus, args,
            collector=lambda pub, *a, **k: reports[str(pub["commit"])])

        self.assertEqual(overall["state"], "failed")
        self.assertEqual([r["name"] for r in overall["repositories"]], ["api", "web"])
        data = kf.read_json(self.bus.result_path("t1"))["data"]
        self.assertEqual(data["deliveries"][0]["publication"]["ci"]["state"], "passed")
        self.assertEqual(data["deliveries"][1]["publication"]["ci"]["state"], "failed")
        self.assertEqual(data["publication"]["ci"]["state"], "failed")


class ForceCompleteWorksetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-fc-ws-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = kf.Bus(os.path.join(self.tmp, "bus"), "run-fc")
        self.bus.ensure_dirs()
        self.bus.write_graph({"nodes": {"t1": {"goal": "g", "deps": [], "kind": "work"}}})
        kf.write_json_atomic(self.bus.meta_path, {"request": "r", "status": "failed"})
        kf.write_json_atomic(self.bus.result_path("t1"), {
            "status": "failed", "output": "publish 失敗",
            "data": {"error_class": "workspace_publish",
                     "publication": {"state": "failed", "repositories": ["api"],
                                     "failed": ["web"]},
                     "deliveries": [
                         {"name": "api", "publication": {
                             "state": "published", "url": "u1", "branch": "af/x",
                             "commit": "a" * 40}},
                         {"name": "web", "publication": {
                             "state": "failed", "name": "web", "url": "u2", "branch": "af/x",
                             "commit": "b" * 40}}]}})

    def test_only_the_failed_element_is_repaired(self):
        result = kf.force_complete_publication(
            self.bus, "run-fc", "手で push した",
            verifier=lambda pub: {"url": pub["url"], "branch": pub["branch"],
                                  "expected_commit": pub["commit"], "remote_tip": "c" * 40})

        self.assertEqual(result["status"], "done")
        self.assertEqual([v.get("name") for v in result["publications"]], ["web"])
        data = kf.read_json(self.bus.result_path("t1"))["data"]
        self.assertEqual(data["deliveries"][0]["publication"]["state"], "published")
        self.assertEqual(data["deliveries"][1]["publication"]["state"], "published-manually")
        self.assertEqual(data["publication"]["state"], "published-manually")
        self.assertNotIn("error_class", data)


class WorksetEndToEndTests(unittest.TestCase):
    """stub executor で 2 リポジトリへ実際に push する（P1 の完了条件）。"""

    # 全 workset 要素へ 1 ファイル書くだけの executor（stub は編集しないので push が起きない）。
    EDITING_EXECUTOR = r'''
def execute(kind, goal, dep_results, model=None, art_dir=None, dep_arts=None,
            repo_instruction="", workspace=None, references=None, workset=None,
            readonly=False):
    import os
    for spec in (workset or []):
        clone = spec.get("clone")
        if clone:
            with open(os.path.join(clone, "edited-%s.txt" % kind), "a") as fh:
                fh.write(goal[:20] + "\n")
    return "[edit] %s" % goal[:40], {"ok": True}
'''

    def test_a_run_with_two_write_targets_publishes_to_both(self):
        tmp = tempfile.mkdtemp(prefix="kf-ws-e2e-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        api = _make_remote(tmp, "api_remote")
        web = _make_remote(tmp, "web_remote")
        plugins = os.path.join(tmp, "executors")
        os.makedirs(plugins)
        pathlib.Path(plugins, "editing.py").write_text(self.EDITING_EXECUTOR, encoding="utf-8")
        # executor プラグインの検索先は設定ファイル経由で子プロセスへ伝える（--config は
        # `_child_base` が引き継ぐ。--executor-dir は親プロセスにしか効かない）。
        config = os.path.join(tmp, "agent-flow.json")
        pathlib.Path(config).write_text(json.dumps({"executor_dir": plugins}), encoding="utf-8")
        bus = os.path.join(tmp, "bus")
        cmd = [sys.executable, str(SCRIPT), "--bus", bus, "--config", config,
               "--workspace", json.dumps({"url": api, "base": "main", "name": "api"}),
               "--workspace", json.dumps({"url": web, "base": "main", "name": "web"}),
               "run", "x", "--workers", "1", "--planner", "stub", "--executor", "editing",
               "--poll", "0.2"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])

        run_id = sorted(os.listdir(os.path.join(bus, "runs")))[0]
        meta = kf.read_json(os.path.join(bus, "runs", run_id, "meta.json"))
        self.assertEqual([e["name"] for e in meta["workspaces"]], ["api", "web"])
        final = kf.read_json(os.path.join(bus, "runs", run_id, "final.json"))
        published = set()
        for result in final["results"].values():
            for record in (result.get("data") or {}).get("deliveries") or []:
                if record["publication"]["state"] == "published":
                    published.add(record["name"])
        self.assertEqual(published, {"api", "web"}, final["results"])
        self.assertTrue(_branch_exists(api, f"af/{run_id}"))
        self.assertTrue(_branch_exists(web, f"af/{run_id}"))


if __name__ == "__main__":
    unittest.main()
