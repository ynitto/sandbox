"""agent-amigos の単体テスト — delivery（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class DeliveryTests(AmigosTestCase):
    """納品棚（accept 時の push 型搬出）。
    設計: docs/plans/2026-07-19-agent-amigos-deliverable-delivery-design.md"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    def drive_to_reviewing(self, spec=None, mid="am-deliv", **kw):
        mid = self.post(spec, mid)
        d = self.daemon(home=self.home, **kw)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) in ("reviewing", "done"):
                break
        return mid, d

    def test_accept_exports_deliverable_and_writes_receipt(self):
        mid, _d = self.drive_to_reviewing()
        self.assertEqual(self.phase(mid), "reviewing")
        accept_mission(self.bus, self.bus.mission(mid), by="owner-node",
                       home=self.home, mission=load_mission(self.bus.mission(mid)))
        self.assertEqual(self.phase(mid), "done")

        rec = read_json(delivery_json(self.home, mid))
        self.assertEqual(rec["mission"], mid)
        self.assertEqual(rec["accepted_by"], "owner-node")
        self.assertFalse(rec["partial"])
        self.assertGreater(rec["execution_seconds"], 0)
        # 成果物の本体が納品棚にあり、MANIFEST は納品書へ置き換わっている
        paths = [f["path"] for f in rec["files"]]
        self.assertIn("architect/architecture.md", paths)
        self.assertIn("impl/src/main.py", paths)
        self.assertTrue(all(f["exported"] for f in rec["files"]))
        for rel in paths:
            self.assertTrue(os.path.isfile(os.path.join(self.home, "deliveries", mid, rel)))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "deliveries", mid, "MANIFEST.json")))
        # 由来ロールとハッシュは MANIFEST から引き継ぐ
        arch = next(f for f in rec["files"] if f["path"] == "architect/architecture.md")
        self.assertEqual(arch["role"], "architect")
        self.assertTrue(arch["sha256_16"])
        # 受領一覧に 1 行増える
        with open(os.path.join(self.home, "DELIVERY.md"), encoding="utf-8") as f:
            index = f.read()
        self.assertIn(mid, index)
        self.assertIn("| 受入日時 |", index)

    def test_oversized_file_is_referenced_not_exported(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        big = os.path.join(mp.deliverable_dir(), "architect", "big.bin")
        with open(big, "wb") as f:
            f.write(b"0" * (delivery.MAX_EXPORT_BYTES + 1))
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        rec = read_json(delivery_json(self.home, mid))
        row = next(f for f in rec["files"] if f["path"] == "architect/big.bin")
        self.assertFalse(row["exported"])
        self.assertEqual(row["skip_reason"], "size")
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "deliveries", mid, "architect", "big.bin")))

    def test_code_deliverable_records_repo_reference_only(self):
        spec = base_spec(workspace={"repo": "ssh://git@gitlab.local/team/faq-bot.git"})
        mid, _d = self.drive_to_reviewing(spec)
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        rec = read_json(delivery_json(self.home, mid))
        self.assertEqual(rec["code"]["repo"], "ssh://git@gitlab.local/team/faq-bot.git")
        self.assertEqual(rec["code"]["branch"], f"amigos/{mid}/integration")

    def test_reject_then_accept_replaces_stale_shelf_contents(self):
        mid, d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        stale = os.path.join(self.home, "deliveries", mid, "architect", "stale.md")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        with open(stale, "w", encoding="utf-8") as f:
            f.write("前ラウンドの残骸")
        # 再 accept（同じミッションの搬出をやり直す）は棚を作り直す
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(delivery_json(self.home, mid)))

    def test_agent_acceptance_exports_too(self):
        spec = base_spec(acceptance="agent")
        mid = self.post(spec, "am-auto")
        d = self.daemon(home=self.home)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "done":
                break
        self.assertEqual(self.phase(mid), "done")
        rec = read_json(delivery_json(self.home, mid))
        self.assertTrue(str(rec["accepted_by"]).startswith("agent:"))
        self.assertEqual(rec["acceptance"], "agent")

    def test_delivery_schema_acceptance_matches_supported_modes(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "schemas", "delivery.schema.json")
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        self.assertEqual(schema["properties"]["acceptance"]["enum"], ["manual", "agent"])

    def test_accept_command_drop_exports_to_home(self):
        from agent_amigos.configfile import commands_dir
        spec = base_spec(staffing_timeout=0)
        mid = self.post(spec, "am-cmd-deliv")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0,
                       commands_home=self.home)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        cdir = commands_dir(self.home)
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "accept.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "accept", "mission": mid}, f)
        d.cycle()
        self.assertEqual(self.phase(mid), "done")
        self.assertTrue(os.path.isfile(delivery_json(self.home, mid)))

    def test_no_home_keeps_accept_working_without_export(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node")     # home 無し = 搬出しない
        self.assertEqual(self.phase(mid), "done")
        self.assertFalse(os.path.exists(deliveries_dir(self.home)))

    def test_gc_keeps_shelf_by_default(self):
        mid, _d = self.drive_to_reviewing()
        mp = self.bus.mission(mid)
        accept_mission(self.bus, mp, by="owner-node", home=self.home,
                       mission=load_mission(mp))
        shelf = os.path.join(self.home, "deliveries", mid)
        os.utime(shelf, (0, 0))
        cli.main(["gc", "--bus", self.bus.root, "--home", self.home, "--keep-days", "0"])
        self.assertFalse(self.bus.mission(mid).exists())   # バスからは消える
        self.assertTrue(os.path.isdir(shelf))              # 納品棚は残る
        cli.main(["gc", "--bus", self.bus.root, "--home", self.home,
                  "--keep-days", "0", "--deliveries-keep-days", "1"])
        self.assertFalse(os.path.isdir(shelf))             # 明示指定でのみ消える


class NodeBudgetTests(AmigosTestCase):
    """ノード予算（P1 拡張、仕様書 §5.2）: 請負側の上限。共有台帳で全ワークロード合計を管理。"""

    def test_zero_config_is_unlimited(self):
        from agent_amigos import nodebudget
        self.assertFalse(nodebudget.state()["exceeded"])   # 設定なし = 0 = 無制限
        mid = self.post()
        d = self.daemon()
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")     # 制限なしで完走

    def test_exhaustion_pauses_and_notifies_owner(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=1.0 / 60)   # ノード上限 = 1 秒
        mid = self.post()
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        # ミッションは failed にならない（ノード予算はノードの都合 — §6.2）
        self.assertNotEqual(self.phase(mid), "failed")
        statuses = [read_json(mp.status(n)) for n in
                    (f"owner-node--{r}" for r in ("architect", "impl", "reviewer"))]
        paused = [s for s in statuses if s and s.get("state") == "paused"]
        self.assertTrue(paused, "ノード予算超過で amigo が paused になるべき")
        self.assertIn("node-budget", paused[0].get("note", ""))
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any("[node-budget]" in m.get("body", "") for m in owner_inbox),
                        "owner へ node-budget 理由の通知が届くべき")
        # 台帳に amigos ワークロードで記帳されている
        self.assertGreater(nodebudget.spent_seconds("day", "amigos"), 0)

    def test_raising_limit_resumes_to_completion(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=1.0 / 60)
        mid = self.post()
        d = self.daemon()
        for _ in range(4):
            d.cycle()
        self.assertNotEqual(self.phase(mid), "reviewing")
        nodebudget.save_config(execution_minutes=0)          # 0 = 無制限へ引き上げ
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")       # paused から復帰して完走

    def test_workload_cap_applies_even_if_total_unlimited(self):
        from agent_amigos import nodebudget
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        nodebudget.save_config(execution_minutes=0,
                               workload_minutes={"amigos": 1.0 / 60})
        # 他ワークロード（定常業務など）の消費は amigos 内訳に影響しない
        nodebudget.record(100.0, workload="routine", tool="agent-loop")
        mid = self.post()
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        statuses = [read_json(mp.status(f"owner-node--{r}"))
                    for r in ("architect", "impl", "reviewer")]
        self.assertTrue(any(s and s.get("state") == "paused" for s in statuses))

    def test_ledger_is_shared_across_workloads(self):
        from agent_amigos import nodebudget
        nodebudget.save_config(execution_minutes=2.0 / 60)   # 合計 2 秒
        nodebudget.record(100.0, workload="project", tool="agent-project")
        # 定常業務・プロジェクトの消費だけで合計上限に達する → amigos は 1 ターンも回せない
        self.assertTrue(nodebudget.state()["exceeded"])


class NodeBudgetV2AndControlTests(AmigosTestCase):
    """ノード予算 v2（トークン一次・rates 推定）と agent-control（上書き・lifecycle・status）。"""

    def setUp(self):
        super().setUp()
        os.environ["AGENT_CONTROL_DIR"] = os.path.join(self.tmp, "control")
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        from agent_amigos import control
        control._CACHE["mtime"] = None

    def _budget(self, cfg):
        d = os.path.join(self.tmp, "node-budget")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def _control(self, ctl):
        from agent_amigos import control
        d = os.path.join(self.tmp, "control")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "control.json"), "w", encoding="utf-8") as f:
            json.dump(ctl, f)
        control._CACHE["mtime"] = None

    def test_token_budget_measured_and_estimated(self):
        from agent_amigos import nodebudget
        self._budget({"version": 2, "tokens": 1000, "rates": {"per_cli": {"claude": 100}}})
        nodebudget.record(8.0, agent_cli="claude")                     # 800 推定
        self.assertFalse(nodebudget.state()["exceeded"])
        nodebudget.record(0.1, tokens_in=150, tokens_out=100)          # +250 = 1050 実測
        st = nodebudget.state()
        self.assertTrue(st["exceeded"])
        self.assertGreaterEqual(st["spent_tokens"], 1000)

    def test_save_config_preserves_v2_keys(self):
        from agent_amigos import nodebudget
        self._budget({"version": 2, "tokens": 500,
                      "allocation": {"soft_ratio": 0.5}, "rates": {"per_cli": {"kiro": 10}}})
        nodebudget.save_config(execution_minutes=5)                    # v1 上限だけ更新
        raw = nodebudget._raw_config()
        self.assertEqual(raw["tokens"], 500)                          # v2 キーを消さない
        self.assertEqual(raw["allocation"]["soft_ratio"], 0.5)
        self.assertEqual(raw["execution_minutes"], 5)

    def test_control_override_and_degraded(self):
        from agent_amigos import control
        self._control({"version": 1, "revision": 4,
                       "workloads": {"amigos": {"agents": {"reviewer": {"model": "opus"}},
                                                "degraded": {"model": "haiku"}}}})
        self.assertEqual(control.override("reviewer"), (None, "opus"))
        self.assertEqual(control.degraded(), (None, "haiku"))

    def test_control_lifecycle_pauses_amigo(self):
        self._control({"version": 1, "workloads": {"amigos": {"lifecycle": "stop"}}})
        mid = self.post()
        d = self.daemon()
        for _ in range(4):
            d.cycle()
        mp = self.bus.mission(mid)
        self.assertNotEqual(self.phase(mid), "failed")               # ミッションは殺さない
        statuses = [read_json(mp.status(f"owner-node--{r}"))
                    for r in ("architect", "impl", "reviewer")]
        paused = [s for s in statuses if s and s.get("state") == "paused"]
        self.assertTrue(paused, "lifecycle=stop で amigo は paused になるべき")
        self.assertIn("agent-control", paused[0].get("note", ""))
