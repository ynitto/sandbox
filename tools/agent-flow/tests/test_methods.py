import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403


class MethodIntegrationTests(unittest.TestCase):
    def _tuning(self):
        return {
            "version": 1, "revision": 1,
            "methods": [{"id": "test-first", "enabled": False,
                         "fragments": [{"role": "worker", "text": "write the test first"}],
                         "when": {"tiers": ["economy"], "max_relative_cost": 1},
                         "description": "d", "origin": "test"}],
            "trials": [{"id": "trial-1", "assignment": "alternating",
                        "variants": [{"id": "baseline", "methods": []},
                                     {"id": "candidate", "methods": ["test-first"]}]}],
        }

    def test_retry_suffix_alternates_and_records_variant(self):
        data = self._tuning()
        with mock.patch.object(kf._methodlib, "load", return_value=data), \
             mock.patch.object(kf._methodlib, "current_tier", return_value="economy"), \
             mock.patch.object(kf._methodlib, "relative_cost", return_value=0.5):
            kf._set_method_context("task-r0", "n1")
            self.assertEqual(kf._apply_methods("prompt", "work", "kiro", "m"), "prompt")
            self.assertEqual(kf._last_methods("work")["trial"]["variant"], "baseline")
            kf._set_method_context("task-r1", "n1")
            prompt = kf._apply_methods("prompt", "work", "kiro", "m")
            self.assertIn("write the test first", prompt)
            self.assertEqual(kf._method_ledger_fields("work")["methods"], ["test-first"])

    def test_node_tier_overrides_control_tier_for_methods(self):
        data = self._tuning()
        with mock.patch.object(kf._methodlib, "load", return_value=data), \
             mock.patch.object(kf._methodlib, "current_tier", return_value="large"), \
             mock.patch.object(kf._methodlib, "relative_cost", return_value=0.5):
            kf._set_method_context("task-r1", "n1")
            prompt = kf._apply_methods("prompt", "work", "kiro", "m", tier="economy")
            self.assertIn("write the test first", prompt)

    def test_fixed_node_tier_is_reported_as_pinned_tier(self):
        control_dir = tempfile.mkdtemp(prefix="kf-control-status-")
        self.addCleanup(shutil.rmtree, control_dir, True)
        pathlib.Path(control_dir, "control.json").write_text(json.dumps({
            "revision": 2, "workloads": {"flow": {"tier": "small"}},
        }), encoding="utf-8")
        with mock.patch.dict(os.environ, {"AGENT_CONTROL_DIR": control_dir}):
            kf._CONTROL_CACHE.update({"mtime": None, "data": {}})
            kf._write_status("codex", "gpt-5", purpose="work", pinned=True, tier="large")
            status = json.loads(next(pathlib.Path(control_dir, "status").glob("agent-flow-*.json"))
                                .read_text(encoding="utf-8"))
        self.assertEqual(status["effective"]["tier"], "large")
        self.assertEqual(status["effective"]["selection_source"], "pinned-tier")
        self.assertTrue(status["effective"]["pinned"])

    # 出力そのものへ何かを書かせる手法（復唱・手順の列挙・入出力契約の宣言）。成果を機械が
    # 解釈する kind へ後置すると 2 通りに壊れる——(1) 出力全体が 1 個の JSON に縛られる役割
    # では本文ではなくキーとして混入する（2026-08-11 実測: filter が
    # `{"task_restatement": ..., "kept": [...]}` を返して 3/6 → 1/6）、(2) 前置きが付くと
    # extract_json が `[` を `{` より先に探すため、オブジェクトの成果が入れ子の配列へ化ける。
    PROSE_EMITTING = ("restate-task", "plan-first", "spec-first", "failure-modes-first")

    def _catalog(self):
        root = pathlib.Path(kf.__file__).resolve().parents[3] / "methods"
        return {"version": 1, "revision": 1, "methods": [
            {**json.loads((root / f"{mid}.json").read_text(encoding="utf-8")), "enabled": True}
            for mid in self.PROSE_EMITTING]}

    def _applied(self, purpose, tier="small"):
        with mock.patch.object(kf._methodlib, "load", return_value=self._catalog()), \
             mock.patch.object(kf._methodlib, "current_tier", return_value=tier), \
             mock.patch.object(kf._methodlib, "relative_cost", return_value=0.0):
            kf._set_method_context("t1", "n1")
            kf._apply_methods("prompt", purpose, "ollama-json", "qwen3.5:9b")
            return kf._last_methods(purpose)["methods"]

    def test_prose_emitting_presets_stay_out_of_machine_read_kinds(self):
        # 対象は「成果を機械が解釈し、かつ worker ロール」の用途。planner / evaluator は同じ
        # JSON 契約でも別ロールで、そちらの fragment は計画の中身を指示するもの（＝契約の
        # 内側に書ける）。verify も別ロールで、本文は散文＋末尾の封筒。
        machine_read = sorted(p for p in kf.JSON_CONTRACT_ROLES | kf.STRUCTURED_KINDS
                              if kf._methodlib.role_for(p) == "worker")
        self.assertTrue(machine_read)
        for purpose in machine_read:
            for tier in ("basic", "small", "medium", "large"):
                self.assertEqual(self._applied(purpose, tier), [],
                                 f"{purpose}（tier={tier}）へ散文を書かせる手法が入っている")

    def test_prose_emitting_presets_still_reach_prose_kinds(self):
        # 上のテストが「カタログを読めていないから空」で通らないための対（両方向で縛る）。
        self.assertEqual(self._applied("work"), sorted(self.PROSE_EMITTING))

    def test_tier_split_directive_keeps_machine_read_discipline(self):
        # split は成果を機械が読む役割（LIST_CONTRACT_ROLES）。手法と同じ規律を tier 指示
        # （プロンプト末尾への後置）にも適用する: 散文を誘発せず、配列のみの出力契約を
        # 指示文の中で再確認する。ここが崩れると _expand_splits が展開されず run が空振りする。
        self.assertTrue(kf.TIER_SPLIT_DIRECTIVES)
        for tier, note in kf.TIER_SPLIT_DIRECTIVES.items():
            self.assertIn("JSON 配列", note, f"tier={tier} の指示が配列契約を再確認していない")
            self.assertIn("説明文", note, f"tier={tier} の指示が散文の禁止を言っていない")

    def test_bus_result_keeps_method_evidence(self):
        root = tempfile.mkdtemp(prefix="kf-method-bus-")
        self.addCleanup(shutil.rmtree, root, True)
        bus = kf.Bus(root, "r1")
        bus.ensure_run("req")
        bus.write_result("n1", "worker", "done", "ok", methods=["test-first"],
                         trial={"id": "trial-1", "variant": "candidate"})
        rec = bus.read_result("n1")
        self.assertEqual(rec["methods"], ["test-first"])
        self.assertEqual(rec["trial"], {"id": "trial-1", "variant": "candidate"})


if __name__ == "__main__":
    unittest.main()
