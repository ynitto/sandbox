"""agent-amigos の単体テスト — cli（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class CliTests(AmigosTestCase):
    def test_post_status_collect_accept(self):
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(), f)
        rc = cli.main(["post", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--design", self.design, "--roles", roles_path,
                       "--mission-id", "am-cli", "--drive", "--agent-cli", "stub",
                       "--cycles", "10", "--interval", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-cli"), "reviewing")
        out = os.path.join(self.tmp, "out")
        rc = cli.main(["collect", "--bus", self.bus.root, "am-cli", "--out", out])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "MANIFEST.json")))
        # 非オーナーは受入できない
        with self.assertRaises(SystemExit):
            cli.main(["accept", "--bus", self.bus.root, "--node-id", "other", "am-cli"])
        home = os.path.join(self.tmp, "home")
        rc = cli.main(["accept", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--home", home, "am-cli"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-cli"), "done")
        # accept は納品棚へ搬出する（push 型納品）
        self.assertTrue(os.path.isfile(
            os.path.join(home, "deliveries", "am-cli", "delivery.json")))
        rc = cli.main(["deliveries", "--bus", self.bus.root, "--home", home, "-v"])
        self.assertEqual(rc, 0)

    def test_cancel_stops_runners(self):
        mid = self.post()
        rc = cli.main(["cancel", "--bus", self.bus.root, "--node-id", "owner-node",
                       mid, "--reason", "test"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase(mid), "cancelled")
        runner = AmigoRunner(self.bus, mid, "impl", "n1", "stub")
        self.assertEqual(runner.turn_once(), "exit")


class DriveTests(AmigosTestCase):
    """単発駆動 `agent-amigos drive`（実装計画 W1-3・設計 §4.5）: 常駐せず cycle() をその場で
    回し、ミッション終端（または --cycles 上限）で戻る。"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    def test_drive_completes_agent_acceptance_mission_and_stops(self):
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(acceptance="agent"), f)
        rc = cli.main(["post", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--design", self.design, "--roles", roles_path,
                       "--mission-id", "am-drive"])
        self.assertEqual(rc, 0)
        rc = cli.main(["drive", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--agent-cli", "stub", "--interval", "0", "--cycles", "20",
                       "--home", self.home])
        self.assertEqual(rc, 0)
        self.assertEqual(self.phase("am-drive"), "done")   # cycles 上限を待たず終端で戻った

    def test_concurrent_drive_and_participate_do_not_double_claim(self):
        """C14 併走（実装計画 W1-12）: スキル起動の単発（`drive`）と常駐体の参加 tick
        （`participate`）が同じ bus を同時に触っても、1 ロールを 2 ノードが持たない。

        設計 §1.3 C14 は「スキル起動と常駐体の併走」を明示的に許す（R9: amigos は常駐なしでも
        成立する）。両者はプロセスが別で、調停は bus 上のファイルだけ——claim の
        決定的タイブレークが効いているかを、実際に 2 ノード名義で同時に走らせて確かめる。"""
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(base_spec(), f)
        self.assertEqual(cli.main(["post", "--bus", self.bus.root, "--node-id", "owner-node",
                                   "--design", self.design, "--roles", roles_path,
                                   "--mission-id", "am-c14"]), 0)

        results, errors = [], []
        barrier = threading.Barrier(2)

        def participate(node: str):
            try:
                barrier.wait(timeout=10)      # 同時に claim へ突っ込ませる
                d = self.daemon(node=node, commands_home=self.tmp)
                results.append((node, d.participate_only()))
            except Exception as e:            # noqa: BLE001 — 失敗内容をアサートで見せる
                errors.append((node, repr(e)))

        threads = [threading.Thread(target=participate, args=(n,))
                   for n in ("pc-drive", "pc-resident")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])

        # 同じ (mission, role) を 2 ノードが担当していないこと（claim の排他）
        owned = [(node, mid, rid) for node, pairs in results for mid, rid in pairs]
        by_role: dict = {}
        for node, mid, rid in owned:
            by_role.setdefault((mid, rid), []).append(node)
        dupes = {k: v for k, v in by_role.items() if len(v) > 1}
        self.assertEqual(dupes, {}, f"同一ロールを複数ノードが担当している: {dupes}")

        # bus 上の勝者も 1 名に定まっていること（観測側とファイルの正が一致する）
        mp = self.bus.mission("am-c14")
        for rid in load_roles(mp):
            w = winner(mp, rid)
            if w is not None:
                self.assertIn(w, ("pc-drive", "pc-resident"))

    def test_run_until_terminal_stops_without_offboard(self):
        # cycle() を差し替えて「1 回目は未終端・2 回目で全終端」を模擬し、until_terminal が
        # cycles 上限を待たずに戻ること・offboard（away 宣言）を呼ばないことを固定する
        # （drive は install_signals=False なので通常経路では offboard に到達しない —
        # ここでは差し替えた cycle() の返り値だけで停止条件を確認する）。
        d = self.daemon()
        calls = [{"m1": "working"}, {"m1": "done"}]
        d.cycle = lambda: calls.pop(0)
        offboarded = {"called": False}
        d.offboard = lambda *a, **k: offboarded.__setitem__("called", True)
        d.run(cycles=0, until_terminal=True, install_signals=False)
        self.assertEqual(calls, [])   # 2 回とも消費 = 2 回目で終端検知して戻った
        self.assertFalse(offboarded["called"])

    def test_run_until_terminal_ignores_empty_missions(self):
        # ミッション未投函（seen 空）の巡は「終端」に数えない。--cycles の安全上限だけが効く。
        d = self.daemon()
        d.cycle = lambda: {}
        d.run(cycles=3, until_terminal=True, install_signals=False)  # cycles 上限で戻る（ハングしない）

    def test_run_until_terminal_with_mission_id_ignores_other_missions(self):
        # mission_id 指定時は他ミッション（共有バス上の無関係な未終端ミッション）を無視し、
        # 対象ミッションだけの終端で戻る（無ければ全ミッション終端待ちになり無限ハングし得る）。
        d = self.daemon()
        d.cycle = lambda: {"m1": "done", "m2": "working"}
        n = {"count": 0}

        def counting_cycle():
            n["count"] += 1
            return {"m1": "done", "m2": "working"}

        d.cycle = counting_cycle
        d.run(cycles=3, until_terminal=True, install_signals=False, mission_id="m1")
        self.assertEqual(n["count"], 1)   # m2 が未終端でも m1 だけを見て 1 巡で戻った

    def test_run_until_terminal_with_mission_id_waits_for_that_mission(self):
        d = self.daemon()
        calls = [{"m1": "working", "m2": "done"}, {"m1": "done", "m2": "done"}]
        d.cycle = lambda: calls.pop(0)
        d.run(cycles=0, until_terminal=True, install_signals=False, mission_id="m1")
        self.assertEqual(calls, [])   # m2 は先に終端していたが、m1 が終端するまで待った


class ParticipateTests(AmigosTestCase):
    """参加のみ tick `participate_only` / `agent-amigos participate`（実装計画 W1-11 残・
    設計 §4.2「amigos 参加（claim・心拍・away）」）: 応募・オーナー職務は行うが手番は
    実行しない（ノード常駐体の短周期 tick 用。実行は別プロセスの `run --once` へ委ねる）。"""

    def test_participate_only_claims_without_running_turns(self):
        self.post()
        d = self.daemon()
        turns_called = []
        d._run_turns = lambda mid, roster: turns_called.append(mid)
        owned = d.participate_only()
        self.assertTrue(owned)               # first-come 単独ノード＝全ロール自己充足
        self.assertEqual(turns_called, [])   # 手番は実行しない
        roster = read_json(self.bus.mission("am-test").roster()) or {}
        for mid, rid in owned:
            self.assertEqual(roster.get(rid, {}).get("node"), d.node_id)

    def test_cycle_dispatch_turns_override(self):
        # dispatch_turns を渡すと既定の _run_turns の代わりに呼ばれる（participate_only の実体）。
        self.post()
        d = self.daemon()
        seen = []
        d.cycle(dispatch_turns=lambda mid, roster: seen.append(mid))
        self.assertEqual(seen, ["am-test"])

    def test_cycle_without_dispatch_turns_still_runs_turns(self):
        # 後方互換: dispatch_turns 省略時は従来どおり _run_turns がインライン実行される。
        self.post()
        d = self.daemon()
        turns_called = []
        d._run_turns = lambda mid, roster: turns_called.append(mid)
        d.cycle()
        self.assertEqual(turns_called, ["am-test"])

    def test_cli_participate_claims_roles_without_progressing_mission(self):
        self.post(mid="am-participate")
        rc = cli.main(["participate", "--bus", self.bus.root, "--node-id", "owner-node",
                       "--agent-cli", "stub", "--json"])
        self.assertEqual(rc, 0)
        roster = read_json(self.bus.mission("am-participate").roster()) or {}
        self.assertTrue(roster)              # 応募が roster に反映されている
        self.assertTrue(all(ent.get("node") == "owner-node" for ent in roster.values()))
        # 手番は実行していない: どのロールの status も未着手のまま
        for rid in roster:
            st = read_json(self.bus.mission("am-participate").status(rid)) or {}
            self.assertNotEqual(st.get("state"), "acted")

    def test_cli_participate_json_stdout_has_no_log_noise(self):
        # 呼び出し側（resident の amigos tick）は stdout を json.loads する。log()（claim 等の
        # 逐次ログ）が stdout に混ざると必ずパース失敗するため、stdout は結果 JSON のみで
        # ログは stderr へ回っていることを固定する（実際に混入していた回帰）。
        self.post(mid="am-stdout")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = cli.main(["participate", "--bus", self.bus.root, "--node-id", "owner-node",
                           "--agent-cli", "stub", "--json"])
        self.assertEqual(rc, 0)
        owned = json.loads(buf_out.getvalue())   # 例外を投げないこと自体が検証
        self.assertTrue(owned)
        self.assertIn("ロール", buf_err.getvalue())   # ログは捨てず stderr に残る


class CommandsIngestTests(AmigosTestCase):
    """commands/ ドロップの取り込み（agent-project の commands/ と同じ結合方式）。"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        from agent_amigos.configfile import commands_dir
        self.cdir = commands_dir(self.home)
        os.makedirs(self.cdir, exist_ok=True)

    def drop(self, name, rec):
        with open(os.path.join(self.cdir, name), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)

    def daemon_home(self, **kw):
        return NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0,
                          commands_home=self.home, **kw)

    def test_post_command_publishes_mission(self):
        self.drop("01-post.json", {
            "command": "post", "title": "依頼", "goal": "g", "mission_id": "am-cmd",
            "design": "# design\n受入基準あり",
            "mission": {"staffing_timeout": 0},
            "roles": [{"id": "impl", "mission": "実装", "deliverables": ["main.py"]}]})
        d = self.daemon_home()
        for _ in range(10):
            d.cycle()
            if self.phase("am-cmd") == "reviewing":
                break
        self.assertEqual(self.phase("am-cmd"), "reviewing")
        # 取り込んだ design doc はホームの状態領域（.agents 移行後は .agents/…）へ永続化される
        from agent_amigos.configfile import state_dir
        self.assertTrue(os.path.isfile(os.path.join(
            state_dir(self.home), "designs", "am-cmd.md")))
        # 処理済みドロップは消える
        self.assertEqual([n for n in os.listdir(self.cdir) if n.endswith(".json")], [])

    def test_claim_command_manual_accept(self):
        # staffing_timeout を伸ばして self-staff（ミッションポリシー）も発動させない
        mid = self.post(base_spec(staffing_timeout=9999))
        d = self.daemon_home(manual_claim=True)
        d.cycle()
        roster = read_json(self.bus.mission(mid).roster()) or {}
        self.assertNotIn("impl", roster)          # 自動では引き受けない
        self.drop("02-claim.json", {"command": "claim", "mission": mid, "role": "impl"})
        d.cycle()
        d.cycle()
        roster = read_json(self.bus.mission(mid).roster()) or {}
        self.assertEqual(roster.get("impl", {}).get("node"), "owner-node")

    def test_bad_command_renamed_rejected(self):
        self.drop("03-bad.json", {"command": "nope"})
        self.drop("04-broken.json", {"command": "claim"})   # mission なし
        d = self.daemon_home()
        d.cycle()
        names = sorted(os.listdir(self.cdir))
        self.assertIn("03-bad.json.rejected", names)
        self.assertIn("04-broken.json.rejected", names)
        d.cycle()                                  # .rejected は再処理されない
        self.assertEqual(sorted(os.listdir(self.cdir)), names)

    def test_manual_claim_keeps_self_staff_off_for_auto_apply_only(self):
        # manual_claim でもオーナー職務（self-staff）は mission ポリシーとして動く
        spec = base_spec()   # staffing_timeout=0 + self-staff
        mid = self.post(spec)
        d = self.daemon_home(manual_claim=True)
        for _ in range(12):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")
