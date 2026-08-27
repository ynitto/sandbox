"""計画承認ゲート（plan gate・オプトイン）の単体・統合テスト。

人間が planner の計画を承認（approved）するまで何も実行させず、差し戻し（rejected＋
コメント）は指摘を planner へ渡して決定的に再計画する。期限切れ・未承認は failed 終端。
"""
from _shared import *  # noqa: F401,F403
import argparse
import pathlib
import shutil
import tempfile
import threading
import time


def _orch_args(root, rid, **kw):
    base = dict(
        config=None, bus=root, git=None, git_branch="main", git_subdir=None,
        lease=30.0, run_id=rid, request="A; B", planner="stub", executor="stub", model=None,
        poll=0.02, max_iterations=3, max_fanout=4, max_retries=2, review=None,
        granularity="finest", exemplar_first=False, cleanup_clone=True,
        keep_clone=False, node_id="orchestrator", workspace=None, references=None,
        inherit_from=None, orphan_grace=0.0, plan_gate=True)
    base.update(kw)
    args = argparse.Namespace(**base)
    kf.resolve_config(args)
    return args


def _reject_data(comment=""):
    return {"interaction_id": "ix-" + "0" * 16, "outcome": "rejected",
            "response_id": "r1", "actor": "tester",
            "answer": {"decision": "rejected", "comment": comment}}


def _approve_data():
    return {"interaction_id": "ix-" + "0" * 16, "outcome": "approved",
            "response_id": "r1", "actor": "tester",
            "answer": {"decision": "approved", "comment": ""}}


class PlanGateHelperTests(unittest.TestCase):
    def test_gate_id_attempt_roundtrip(self):
        self.assertEqual(kf.plan_gate_id(1), "plan-gate")
        self.assertEqual(kf.plan_gate_id(3), "plan-gate-3")
        self.assertEqual(kf.plan_gate_attempt("plan-gate"), 1)
        self.assertEqual(kf.plan_gate_attempt("plan-gate-4"), 4)
        self.assertIsNone(kf.plan_gate_attempt("gate"))
        self.assertIsNone(kf.plan_gate_attempt("plan-gate-x"))

    def test_gate_task_shape_and_timeout(self):
        tasks = [{"id": "t1", "goal": "g1", "deps": [], "kind": "work"}]
        gate = kf.plan_gate_task("req", {"patterns": ["fan-out-and-synthesize"],
                                         "parallelism": 2}, tasks, attempt=2, timeout=120)
        self.assertEqual(gate["id"], "plan-gate-2")
        self.assertEqual(gate["kind"], "human")
        self.assertEqual(gate["interaction"]["mode"], "approval")
        self.assertEqual(gate["interaction"]["timeout_seconds"], 120)
        # normalize_spec 済み（park 時に再正規化しても等値）
        self.assertEqual(kf._interaction.normalize_spec(gate["interaction"]),
                         gate["interaction"])

    def test_gate_task_default_timeout_is_interaction_default(self):
        gate = kf.plan_gate_task("req", {}, [], attempt=1, timeout=0)
        self.assertEqual(gate["interaction"]["timeout_seconds"],
                         kf._interaction.DEFAULT_TIMEOUT_SECONDS)

    def test_prompt_lists_nodes_and_excludes_human(self):
        tasks = [
            {"id": "t1", "goal": "調査する", "deps": [], "kind": "work"},
            {"id": "synth", "goal": "統合", "deps": ["t1"], "kind": "synthesize"},
            {"id": "plan-gate", "goal": "承認", "deps": [], "kind": "human"},
        ]
        p = kf.render_plan_gate_prompt("要求本文", {"patterns": ["fan-out-and-synthesize"],
                                                    "parallelism": 2, "tier": "basic",
                                                    "granularity": "finest"}, tasks)
        self.assertIn("t1 [work]", p)
        self.assertIn("synth [synthesize] deps=t1", p)
        self.assertNotIn("plan-gate", p)
        self.assertIn("tier basic", p)
        self.assertIn("差し戻し", p)

    def test_prompt_is_bounded(self):
        tasks = [{"id": f"t{i}", "goal": "x" * 400, "deps": [], "kind": "work"}
                 for i in range(500)]
        p = kf.render_plan_gate_prompt("req", {}, tasks)
        self.assertLessEqual(len(p), kf._PLAN_GATE_PROMPT_LIMIT + 8)

    def test_apply_plan_gate_rewires_roots_only(self):
        tasks = [
            {"id": "base-sync", "goal": "sync", "deps": [], "kind": "base-sync"},
            {"id": "t1", "goal": "g1", "deps": ["base-sync"], "kind": "work"},
            {"id": "synth", "goal": "統合", "deps": ["t1"], "kind": "synthesize"},
        ]
        nodes = {t["id"]: kf._node_entry(t) for t in tasks}
        gate = kf.plan_gate_task("req", {}, tasks)
        kf.apply_plan_gate(nodes, tasks, gate)
        self.assertEqual(nodes["base-sync"]["deps"], ["plan-gate"])
        self.assertEqual(nodes["t1"]["deps"], ["base-sync"])       # 非 root は不変
        self.assertEqual(nodes["plan-gate"]["deps"], [])
        self.assertEqual(tasks[-1]["id"], "plan-gate")
        # task 列側の root も同じ付け替え
        self.assertEqual(tasks[0]["deps"], ["plan-gate"])
        # graph へ interaction が残る（worker が claim 時に読む）
        self.assertEqual(nodes["plan-gate"]["interaction"]["mode"], "approval")

    def test_node_entry_keeps_interaction(self):
        # ユーザー定義フローの human ノードも同じ経路で graph に載る（潜在バグの固定）
        t = {"id": "review", "goal": "人の確認", "deps": [], "kind": "human",
             "interaction": {"mode": "approval", "prompt": "進めますか",
                             "audience": ["reviewer"], "timeout_seconds": 60}}
        e = kf._node_entry(t)
        self.assertEqual(e["interaction"], t["interaction"])

    def test_plan_gate_state_transitions(self):
        nodes = {"plan-gate": {"kind": "human", "deps": []},
                 "t1": {"kind": "work", "deps": ["plan-gate"]}}
        self.assertIsNone(kf.plan_gate_state({"t1": nodes["t1"]}, {}))
        self.assertEqual(kf.plan_gate_state(nodes, {})["state"], "waiting")
        self.assertEqual(
            kf.plan_gate_state(nodes, {"plan-gate": {"status": "done",
                                                     "data": _approve_data()}})["state"],
            "approved")
        gs = kf.plan_gate_state(nodes, {"plan-gate": {
            "status": "failed", "data": _reject_data("もっと分割して")}})
        self.assertEqual(gs["state"], "rejected")
        self.assertEqual(gs["comment"], "もっと分割して")
        self.assertEqual(gs["attempt"], 1)
        expired = {"interaction_id": "ix-" + "0" * 16, "outcome": "expired",
                   "actor": "system", "answer": {}}
        self.assertEqual(
            kf.plan_gate_state(nodes, {"plan-gate": {"status": "failed",
                                                     "data": expired}})["state"],
            "unapproved")

    def test_plan_gate_state_uses_latest_gate(self):
        nodes = {"plan-gate-2": {"kind": "human", "deps": []},
                 "t1": {"kind": "work", "deps": ["plan-gate-2"]}}
        gs = kf.plan_gate_state(nodes, {"plan-gate-2": {"status": "failed",
                                                        "data": _reject_data("x")}})
        self.assertEqual((gs["attempt"], gs["id"]), (2, "plan-gate-2"))

    def test_dedupe_task_ids_renames_collisions_and_deps(self):
        tasks = [{"id": "t1", "goal": "a", "deps": [], "kind": "work"},
                 {"id": "synth", "goal": "b", "deps": ["t1"], "kind": "synthesize"}]
        out = kf.dedupe_task_ids(tasks, {"t1"})
        self.assertEqual(out[0]["id"], "t1-g2")
        self.assertEqual(out[1]["deps"], ["t1-g2"])

    def test_feedback_request_appends_latest_comment_only(self):
        r = kf.plan_gate_feedback_request("元要求", "粒度が粗い")
        self.assertTrue(r.startswith("元要求"))
        self.assertIn(kf.PLAN_GATE_FEEDBACK_MARK, r)
        self.assertIn("粒度が粗い", r)
        r2 = kf.plan_gate_feedback_request("元要求", "")
        self.assertIn("コメント無し", r2)

    def test_gate_enabled_is_opt_in_and_skips_user_plans(self):
        on = types.SimpleNamespace(plan_gate=True)
        off = types.SimpleNamespace(plan_gate=False)
        self.assertTrue(kf._plan_gate_enabled(on, {"patterns": ["fan-out-and-synthesize"]}))
        self.assertFalse(kf._plan_gate_enabled(off, {}))
        self.assertFalse(kf._plan_gate_enabled(on, {"user_plan": True}))

    def test_rejected_comment_flows_into_human_feedback(self):
        results = {"g": {"status": "failed", "data": _reject_data("Xを直して")},
                   "ok": {"status": "done", "data": _approve_data()}}
        fb = kf.human_feedback_from_results(results)
        self.assertIn("[g] Xを直して", fb)
        # 承認コメントは拾わない（承認済み run へ不要な replan を誘発しない）
        results2 = {"ok": {"status": "done", "data": {
            **_approve_data(), "answer": {"decision": "approved", "comment": "LGTM あとで X も"}}}}
        self.assertEqual(kf.human_feedback_from_results(results2), "")


class PlanGateArgvTests(unittest.TestCase):
    """--plan-gate は子（orchestrator）へ転送され、実パーサで parse できること。"""

    def test_spawn_orchestrator_forwards_plan_gate(self):
        args = types.SimpleNamespace(
            granularity="finest", exemplar_first=False, planner="stub", executor="agent",
            max_iterations=1, max_fanout=4, max_retries=3, model=None, poll=1.0,
            plan_gate=True, plan_gate_timeout=3600.0)
        captured = {}

        def fake_popen(cmd, *a, **kw):
            captured["cmd"] = cmd
            return object()

        with mock.patch.object(kf.subprocess, "Popen", side_effect=fake_popen):
            kf._spawn_orchestrator([sys.executable, kf.self_path(), "--bus", "/tmp/bus"],
                                   args, "run-42", {"request": "x"})
        parsed = kf.build_parser().parse_args(captured["cmd"][2:])
        self.assertIs(parsed.plan_gate, True)
        self.assertEqual(parsed.plan_gate_timeout, 3600.0)
        self.assertEqual(parsed.cmd, "orchestrate")


class PlanGateOrchestrateTests(unittest.TestCase):
    """cmd_orchestrate の統合: 挿入 → 差し戻し再計画（有界）→ 承認 → 完走。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kf-plangate-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _start(self, args):
        t = threading.Thread(target=kf.cmd_orchestrate, args=(args,), daemon=True)
        t.start()
        return t

    def _wait(self, fn, timeout=10.0, msg="条件が満たされない"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = fn()
            if v:
                return v
            time.sleep(0.02)
        self.fail(msg)

    def test_gate_blocks_roots_then_rejections_replan_until_capped(self):
        rid = "run-gate-reject"
        args = _orch_args(self.root, rid, max_retries=1)
        bus = kf.Bus(self.root, rid)
        t = self._start(args)

        graph = self._wait(lambda: (bus.read_graph() or {}).get("nodes", {}).get("plan-gate")
                           and bus.read_graph(), msg="plan-gate が挿入されない")
        nodes = graph["nodes"]
        self.assertEqual(nodes["plan-gate"]["kind"], "human")
        for nid, n in nodes.items():
            if nid != "plan-gate":
                self.assertTrue(n["deps"], f"{nid} が gate を経ずに root のまま")
        self.assertEqual(graph["strategy"]["plan_gate"], "plan-gate")
        # task ファイルにも interaction 込みで書かれている（worker が park できる）
        task = json.loads(pathlib.Path(bus.tasks_dir, "plan-gate.json").read_text())
        self.assertEqual(task["interaction"]["mode"], "approval")

        # 差し戻し #1 → 指摘を反映して再計画（plan-gate-2）。旧 gate はグラフから消える
        bus.write_result("plan-gate", "human", "failed", "human interaction: rejected",
                         _reject_data("粒度が粗い。もっと分割して"), kind="human")
        # graph と plan-gate-replan イベントの両方を待つ（graph だけ見ると、event 書き込み前に
        # 進んで空振りするレースがある）。
        def _replan_ready():
            g = bus.read_graph() or {}
            if "plan-gate-2" not in g.get("nodes", {}):
                return None
            evs = [e for e in bus.recent_events(50) if e.get("kind") == "plan-gate-replan"]
            return (g, evs) if evs else None
        graph, events = self._wait(_replan_ready, msg="差し戻し後に再計画されない")
        self.assertNotIn("plan-gate", graph["nodes"])
        for nid, n in graph["nodes"].items():
            if nid != "plan-gate-2":
                self.assertTrue(n["deps"], f"{nid} が新 gate を経ずに root のまま")
        self.assertEqual(events[-1]["attempt"], 1)

        # 差し戻し #2 は max_retries=1 を超過 → [plan-gate] で failed 終端
        bus.write_result("plan-gate-2", "human", "failed", "human interaction: rejected",
                         _reject_data("まだ駄目"), kind="human")
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "orchestrator が終了しない")
        meta = bus.run_meta(rid)
        self.assertEqual(meta["status"], "failed")
        self.assertIn("[plan-gate]", meta["failure_reason"])
        self.assertIn("まだ駄目", meta["failure_reason"])

    def test_gate_approval_unblocks_and_run_completes(self):
        rid = "run-gate-approve"
        args = _orch_args(self.root, rid)
        bus = kf.Bus(self.root, rid)
        with mock.patch.object(kf, "run_verification_plan", return_value=None):
            t = self._start(args)
            graph = self._wait(lambda: (bus.read_graph() or {}).get("nodes", {}).get("plan-gate")
                               and bus.read_graph(), msg="plan-gate が挿入されない")
            bus.write_result("plan-gate", "human", "done", "human interaction: approved",
                             _approve_data(), kind="human")
            # テストが worker を代行: gate 以外のノードへ結果を書く
            for nid, node in graph["nodes"].items():
                if nid == "plan-gate":
                    continue
                data = {"ok": True} if node.get("kind") == "verify" else None
                bus.write_result(nid, "w1", "done",
                                 "verify=pass" if node.get("kind") == "verify" else "ok", data,
                                 kind=node.get("kind"))
            t.join(timeout=15)
        self.assertFalse(t.is_alive(), "orchestrator が終了しない")
        self.assertEqual(bus.run_meta(rid)["status"], "done")

    def test_gate_expiry_fails_closed(self):
        rid = "run-gate-expire"
        args = _orch_args(self.root, rid)
        bus = kf.Bus(self.root, rid)
        t = self._start(args)
        self._wait(lambda: (bus.read_graph() or {}).get("nodes", {}).get("plan-gate"),
                   msg="plan-gate が挿入されない")
        bus.write_result("plan-gate", "human", "failed", "human interaction: expired",
                         {"interaction_id": "ix-" + "0" * 16, "outcome": "expired",
                          "actor": "system", "answer": {}}, kind="human")
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "orchestrator が終了しない")
        meta = bus.run_meta(rid)
        self.assertEqual(meta["status"], "failed")
        self.assertIn("[plan-gate]", meta["failure_reason"])
        self.assertIn("承認が得られませんでした", meta["failure_reason"])

    def test_no_gate_without_opt_in(self):
        rid = "run-no-gate"
        args = _orch_args(self.root, rid, plan_gate=False)
        bus = kf.Bus(self.root, rid)
        with mock.patch.object(kf, "run_verification_plan", return_value=None):
            t = self._start(args)
            graph = self._wait(lambda: bus.read_graph(), msg="計画されない")
            self.assertTrue(all(kf.plan_gate_attempt(nid) is None for nid in graph["nodes"]))
            for nid, node in graph["nodes"].items():
                data = {"ok": True} if node.get("kind") == "verify" else None
                bus.write_result(nid, "w1", "done",
                                 "verify=pass" if node.get("kind") == "verify" else "ok", data,
                                 kind=node.get("kind"))
            t.join(timeout=15)
        self.assertFalse(t.is_alive())
        self.assertEqual(bus.run_meta(rid)["status"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
