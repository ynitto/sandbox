"""agent-amigos の単体テスト — turns（`test_agent_amigos.py` から機能別に分割）。

共有の前置き（環境隔離・モジュールのロード・`AmigosTestCase`）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）


class BudgetTests(AmigosTestCase):
    def test_wrap_up_partial_delivery_on_exhaustion(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"      # 1 ターン = 1 秒消費
        spec = base_spec(budget={"execution_minutes": 1.0 / 60})   # 予算 1 秒
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(10):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        mp = self.bus.mission(mid)
        manifest = read_json(mp.manifest())
        self.assertIsNotNone(manifest, "予算枯渇後に wrap-up 統合されるべき")
        self.assertTrue(manifest["partial"])
        self.assertEqual(manifest["reason"], "budget")

    def test_on_exhausted_fail_terminates(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(budget={"execution_minutes": 1.0 / 60, "on_exhausted": "fail"})
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(10):
            d.cycle()
        self.assertEqual(self.phase(mid), "failed")

    def test_budget_add_reopens_headroom(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(budget={"execution_minutes": 1.0 / 60})
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(6):
            d.cycle()
        rc = cli.main(["budget", "--bus", self.bus.root, "--node-id", "owner-node",
                       "add", mid, "--minutes", "60"])
        self.assertEqual(rc, 0)
        mp = self.bus.mission(mid)
        mission = load_mission(mp)
        cs = convergence_state(mission, load_roles(mp), mp)
        self.assertFalse(cs["budget"]["hard"])


class QuiescenceTests(AmigosTestCase):
    def test_quiescence_converges_partial(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        mission = load_mission(mp)
        # 全ワーカーが「完了宣言なし・静穏」の状態を手書きで再現する
        write_json_atomic(mp.roster(), {rid: {"node": "n1"} for rid in roles})
        for rid in roles:
            write_json_atomic(mp.status(f"n1--{rid}"),
                              {"node": "n1", "role": rid, "idle_turns": 5,
                               "done_round": None, "approved_round": None})
        cs = convergence_state(mission, roles, mp)
        self.assertTrue(cs["converged"])
        self.assertEqual(cs["reason"], "quiescence")
        self.assertTrue(cs["partial"])

    def test_unanswered_question_blocks_quiescence(self):
        mid = self.post()
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        mission = load_mission(mp)
        write_json_atomic(mp.roster(), {rid: {"node": "n1"} for rid in roles})
        for rid in roles:
            write_json_atomic(mp.status(f"n1--{rid}"),
                              {"node": "n1", "role": rid, "idle_turns": 5,
                               "done_round": None, "approved_round": None})
        from agent_amigos.messages import build_message, message_path
        _mid, msg = build_message("impl", "architect", "question", "q", "?")
        write_json_atomic(message_path(mp, msg), msg)
        cs = convergence_state(mission, roles, mp)
        self.assertFalse(cs["converged"])


class EscalationTests(AmigosTestCase):
    def test_stale_question_escalates_to_owner(self):
        spec = base_spec()
        # architect を任意ロールにし、誰も担当しない状態を作る（質問が放置される）
        spec["roles"][0]["required"] = False
        mid = self.post(spec)
        # owner は architect を claim しない
        d = self.daemon(roles_filter=["impl", "reviewer"])
        for _ in range(6):
            d.cycle()
        mp = self.bus.mission(mid)
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any(m["type"] == "decision-request" for m in owner_inbox),
                        "未回答質問が question_timeout 後に owner へ昇格されるべき")


class AcceptanceAgentTests(AmigosTestCase):
    """acceptance: agent（P2、仕様書 §9）: オーナーノードの自動受入判定。
    stub 判定は決定的（partial → 差し戻し、完全 → 受入）。"""

    def test_auto_accept_full_delivery(self):
        spec = base_spec(acceptance="agent")
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(14):
            d.cycle()
            if self.phase(mid) == "done":
                break
        self.assertEqual(self.phase(mid), "done")     # 人の accept なしで done に到達
        mp = self.bus.mission(mid)
        final = read_json(mp.final())
        self.assertTrue(final["accepted"])
        self.assertTrue(str(final["by"]).startswith("agent:"))

    def test_partial_rejected_then_escalates_to_human(self):
        os.environ["AGENT_AMIGOS_STUB_COST"] = "1.0"
        spec = base_spec(acceptance="agent",
                         convergence={"done_when": "all-required-done", "review_rounds": 2},
                         budget={"execution_minutes": 1.0 / 60})   # 予算枯渇 → partial 納品
        mid = self.post(spec)
        d = self.daemon()
        for _ in range(20):
            d.cycle()
        mp = self.bus.mission(mid)
        # 自動判定は partial を差し戻し続けるが review_rounds で止まり、人へ委ねる
        self.assertNotEqual(self.phase(mid), "done")
        rejections = sorted(os.listdir(mp.rejections_dir()))
        self.assertEqual(len(rejections), 2)          # 上限 review_rounds=2 で停止
        owner_inbox = read_inbox(mp, "owner")
        self.assertTrue(any(m["type"] == "decision-request"
                            and "受入の自動判定" in m.get("subject", "")
                            for m in owner_inbox))
        # final は書かれていない（done を作れるのは人の判断のみ）
        self.assertIsNone(read_json(mp.final()))


class TurnMarkTests(AmigosTestCase):
    """手番マーカー（実装計画 W1-5）: PC 単位の同時実行上限の根拠。

    上限は常駐体（agent-project）が守るが、その観測対象になるのがこのファイル。
    バスの `status/<who>.json` は**在籍状態**で、手番が終わっても `working` のまま残る
    ——あれを走行中と読むと、常駐体が回したロールの次の手番を自分で永久に弾く。"""

    def test_mark_exists_during_turn_and_is_removed_after(self):
        from agent_amigos import turnmark
        mid = self.post()
        self.daemon().cycle()                      # claim させる
        seen = {}

        class Spy(AmigoRunner):
            def _turn_once(self_):
                seen["during"] = turnmark.running()    # ターンの最中
                return super()._turn_once()

        Spy(self.bus, mid, "architect", "owner-node", agent_cli="stub").turn_once()
        self.assertEqual(seen["during"], {(mid, "architect")})
        self.assertEqual(turnmark.running(), set(), "ターン後にマーカーが残っている")

    def test_dead_process_mark_is_ignored_and_cleaned(self):
        from agent_amigos import turnmark
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        os.makedirs(self.turns_dir, exist_ok=True)
        path = os.path.join(self.turns_dir, "am-x--impl.json")
        write_json_atomic(path, {"pid": dead.pid, "mission": "am-x", "role": "impl"})
        self.assertEqual(turnmark.running(), set())
        self.assertFalse(os.path.exists(path), "落ちたプロセスの書き残しが残っている")

    def test_finished_turn_leaves_working_state_in_bus(self):
        """在籍状態を走行中と読めない理由を事実として固定する。

        `status/<who>.json` の `state` はターン終了後も `working`。常駐体側の観測は
        この値を見てはいけない（見ると自分の次の手番を弾く）。"""
        mid = self.post()
        self.daemon().cycle()
        AmigoRunner(self.bus, mid, "architect", "owner-node", agent_cli="stub").turn_once()
        st = read_json(self.bus.mission(mid).status("owner-node--architect")) or {}
        self.assertEqual(st.get("state"), "working")


class SeatsAggregationTests(AmigosTestCase):
    """G1（seats>1・並列同一シート）と G2（integrator の決定的集約）。"""

    def _seats_spec(self, mode, seats=3, **extra):
        role = {"id": "solver", "mission": "独立に解く", "seats": seats,
                "deliverables": ["ANSWER.md"]}
        if mode:
            role["aggregate"] = mode
        role.update(extra)
        return {"mission": {"title": "t", "goal": "g", "staffing_timeout": 0,
                            "convergence": {"done_when": "all-required-done",
                                            "quiescence_turns": 9}},
                "roles": [role]}

    def _aggregate(self, mid, answers, scores=None):
        """指定した席回答（と任意の SCORE）を artifacts へ書き、集約だけを走らせる。"""
        from agent_amigos.runner import AmigoRunner
        from agent_amigos.bus import TurnTxn
        mp = self.bus.mission(mid)
        for sid, ans in answers.items():
            if ans is not None:
                p = os.path.join(mp.artifacts_dir(sid), "ANSWER.md")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(ans)
        for sid, sc in (scores or {}).items():
            p = os.path.join(mp.artifacts_dir(sid), "SCORE")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(str(sc))
        runner = AmigoRunner(self.bus, mid, "integrator", "owner-node")
        txn = TurnTxn()
        summary = runner._aggregate_seat_groups(txn, load_roles(mp))
        txn.apply(self.bus)
        agg = read_json(os.path.join(mp.deliverable_dir(), "solver", "AGGREGATE.json"))
        return summary[0], agg

    # --- G1: 展開・検証 ------------------------------------------------------
    def test_seats_expand_into_concrete_roles(self):
        _m, roles = normalize_mission(self._seats_spec("majority", seats=3))
        ids = {r["id"] for r in roles}
        self.assertEqual({"solver#0", "solver#1", "solver#2", "integrator"}, ids)
        s0 = next(r for r in roles if r["id"] == "solver#0")
        self.assertEqual(s0["seat_group"], "solver")
        self.assertEqual(s0["seat_count"], 3)
        self.assertEqual(s0["aggregate"], "majority")

    def test_collaborates_with_remapped_to_seats(self):
        spec = {"mission": {"title": "t", "goal": "g"},
                "roles": [{"id": "solver", "mission": "解く", "seats": 2},
                          {"id": "reviewer", "mission": "見る", "approver": True,
                           "collaborates_with": ["solver"]}]}
        _m, roles = normalize_mission(spec)
        rv = next(r for r in roles if r["id"] == "reviewer")
        self.assertEqual(sorted(rv["collaborates_with"]), ["solver#0", "solver#1"])

    def test_seats_validation(self):
        with self.assertRaises(SystemExit):                     # aggregate on seats<2
            normalize_mission({"roles": [{"id": "x", "seats": 1, "aggregate": "majority"}]})
        with self.assertRaises(SystemExit):                     # unknown aggregate
            normalize_mission({"roles": [{"id": "x", "seats": 2, "aggregate": "nope"}]})
        with self.assertRaises(SystemExit):                     # seats < 1
            normalize_mission({"roles": [{"id": "x", "seats": 0}]})
        with self.assertRaises(SystemExit):                     # '#' reserved in id
            normalize_mission({"roles": [{"id": "a#0"}]})

    # --- G2: 集約モード ------------------------------------------------------
    def test_aggregate_majority(self):
        mid = self.post(self._seats_spec("majority"), mid="am-maj")
        summary, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"})
        self.assertEqual(summary["mode"], "majority")
        self.assertEqual(agg["winner"], "A")
        self.assertEqual(agg["votes"], 3)
        self.assertEqual(agg["tally"], {"A": 2, "B": 1})
        self.assertFalse(agg["agreed"])
        # 勝者が AGGREGATE.md に書かれる
        md = os.path.join(self.bus.mission(mid).deliverable_dir(), "solver", "AGGREGATE.md")
        with open(md, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "A")

    def test_aggregate_majority_tiebreak_is_deterministic(self):
        mid = self.post(self._seats_spec("majority", seats=2), mid="am-tie")
        _s, agg = self._aggregate(mid, {"solver#0": "B", "solver#1": "A"})
        self.assertEqual(agg["winner"], "A")     # 得票同数は回答昇順で決定的

    def test_aggregate_consensus(self):
        mid = self.post(self._seats_spec("consensus"), mid="am-con")
        _s, agg = self._aggregate(mid, {"solver#0": "X", "solver#1": "X", "solver#2": "X"})
        self.assertTrue(agg["agreed"])
        self.assertEqual(agg["winner"], "X")
        mid2 = self.post(self._seats_spec("consensus"), mid="am-con2")
        _s2, agg2 = self._aggregate(mid2, {"solver#0": "X", "solver#1": "Y", "solver#2": "X"})
        self.assertFalse(agg2["agreed"])          # 割れたら agreed=false（最頻値は X）
        self.assertEqual(agg2["winner"], "X")

    def test_aggregate_gather(self):
        mid = self.post(self._seats_spec("gather"), mid="am-gat")
        summary, _agg = self._aggregate(mid, {"solver#0": "one", "solver#1": "two",
                                              "solver#2": "three"})
        self.assertEqual(summary["mode"], "gather")
        self.assertEqual(summary["collected"], 3)
        md = os.path.join(self.bus.mission(mid).deliverable_dir(), "solver", "AGGREGATE.md")
        with open(md, encoding="utf-8") as f:
            body = f.read()
        for token in ("solver#0", "solver#1", "solver#2", "one", "two", "three"):
            self.assertIn(token, body)

    def test_aggregate_missing_answers_are_skipped(self):
        mid = self.post(self._seats_spec("majority"), mid="am-miss")
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": None})
        self.assertEqual(agg["votes"], 2)         # 未回答席は票に数えない
        self.assertEqual(agg["winner"], "A")
        self.assertFalse(agg["seats"]["solver#2"]["present"])

    def test_aggregate_weighted_vote(self):
        mid = self.post(self._seats_spec("weighted-vote"), mid="am-wv")
        # A が 2 席・B が 1 席だが、B の重みが大きいので B が勝つ
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"},
                                  scores={"solver#0": 1, "solver#1": 1, "solver#2": 5})
        self.assertEqual(agg["winner"], "B")
        self.assertEqual(agg["tally"], {"A": 2.0, "B": 5.0})

    def test_aggregate_weighted_vote_defaults_to_one(self):
        mid = self.post(self._seats_spec("weighted-vote"), mid="am-wv2")
        _s, agg = self._aggregate(mid, {"solver#0": "A", "solver#1": "A", "solver#2": "B"})
        self.assertEqual(agg["winner"], "A")     # 重み未指定は 1.0 = majority と同じ

    def test_aggregate_approval_count(self):
        mid = self.post(self._seats_spec("approval-count"), mid="am-ap")
        # スコア最大の候補（席）が勝つ
        _s, agg = self._aggregate(mid, {"solver#0": "X", "solver#1": "Y", "solver#2": "Z"},
                                  scores={"solver#0": 2, "solver#1": 9, "solver#2": 3})
        self.assertEqual(agg["winner"], "Y")
        self.assertEqual(agg["winner_seat"], "solver#1")
        self.assertEqual(agg["winner_score"], 9.0)

    # --- done_when: consensus（早期収束） ------------------------------------
    def _stage_consensus(self, spec, answers, mid):
        self.post(spec, mid=mid)
        mp = self.bus.mission(mid)
        roles = load_roles(mp)
        write_json_atomic(mp.roster(), {rid: {"node": "owner-node"} for rid in roles})
        for sid, ans in answers.items():
            p = os.path.join(mp.artifacts_dir(sid), "ANSWER.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(ans)
        return convergence_state(load_mission(mp), roles, mp)

    def test_done_when_consensus_converges_when_ratio_met(self):
        spec = self._seats_spec("majority")
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 2, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "A",
                                          "solver#2": "B"}, "am-cons-y")   # 2/3 = 0.66
        self.assertTrue(cs["converged"])
        self.assertEqual(cs["reason"], "done")

    def test_done_when_consensus_waits_when_split(self):
        spec = self._seats_spec("majority")
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 2, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "B",
                                          "solver#2": "C"}, "am-cons-n")   # 1/3 < 0.6
        self.assertFalse(cs["converged"])

    def test_done_when_consensus_needs_min_answers(self):
        spec = self._seats_spec("majority", seats=5)
        spec["mission"]["convergence"] = {"done_when": "consensus", "consensus_ratio": 0.6,
                                          "consensus_min": 3, "quiescence_turns": 0}
        cs = self._stage_consensus(spec, {"solver#0": "A", "solver#1": "A"},
                                   "am-cons-min")    # 一致だが回答 2 < min 3
        self.assertFalse(cs["converged"])

    # --- E2E: stub で seats が統合まで到達し manifest に集約が載る ------------
    def test_seats_end_to_end_stub_produces_aggregates(self):
        mid = self.post(self._seats_spec("majority"), mid="am-e2e")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0)
        for _ in range(25):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")
        man = read_json(self.bus.mission(mid).manifest())
        aggs = {a["group"]: a for a in man.get("aggregates") or []}
        self.assertIn("solver", aggs)
        self.assertEqual(aggs["solver"]["mode"], "majority")
        self.assertEqual(aggs["solver"]["votes"], 3)   # 3 席とも ANSWER.md を書いた


class MessageDeliveryTests(AmigosTestCase):
    def test_delayed_lower_id_is_still_new(self):
        from agent_amigos.messages import message_path, new_messages
        mid = self.post(mid="am-msg-order")
        mp = self.bus.mission(mid)
        high = {"id": "0002", "from": "impl", "to": "architect", "type": "info"}
        write_json_atomic(message_path(mp, high), high)
        fresh, seen = new_messages(mp, "architect", [])
        self.assertEqual([m["id"] for m in fresh], ["0002"])

        low = {"id": "0001", "from": "impl", "to": "architect", "type": "answer"}
        write_json_atomic(message_path(mp, low), low)
        fresh, seen = new_messages(mp, "architect", seen)
        self.assertEqual([m["id"] for m in fresh], ["0001"])
        self.assertEqual(seen, ["0001", "0002"])

    def test_cursor_migration_replays_answer_to_open_question(self):
        from agent_amigos.messages import message_path, new_messages
        mid = self.post(mid="am-msg-migrate")
        mp = self.bus.mission(mid)
        answer = {"id": "0001", "from": "impl", "to": "architect", "type": "answer",
                  "reply_to": "question-1"}
        write_json_atomic(message_path(mp, answer), answer)
        fresh, seen = new_messages(mp, "architect", None, legacy_cursor="0002",
                                   open_questions={"question-1"})
        self.assertEqual([m["id"] for m in fresh], ["0001"])
        self.assertEqual(seen, ["0001"])

    def test_inbox_and_all_duplicate_id_is_delivered_once(self):
        from agent_amigos.messages import message_path, new_messages
        mid = self.post(mid="am-msg-dedup")
        mp = self.bus.mission(mid)
        direct = {"id": "same", "from": "impl", "to": "architect", "type": "info"}
        broadcast = {**direct, "to": "all"}
        write_json_atomic(message_path(mp, direct), direct)
        write_json_atomic(message_path(mp, broadcast), broadcast)
        fresh, _ = new_messages(mp, "architect", [])
        self.assertEqual([m["id"] for m in fresh], ["same"])


class DebateRoundsTests(AmigosTestCase):
    """G3: 同期討論ラウンド（ラウンドバリア）。"""

    def _debate_spec(self, seats=3, rounds=3, done_when="all-required-done", **conv):
        c = {"done_when": done_when, "quiescence_turns": 99}
        c.update(conv)
        return {"mission": {"title": "d", "goal": "g", "staffing_timeout": 0, "convergence": c},
                "roles": [{"id": "debater", "mission": "立場を論じる", "seats": seats,
                           "rounds": rounds, "aggregate": "majority",
                           "deliverables": ["ANSWER.md"]}]}

    def _round_files(self, mid, seat_id):
        mp = self.bus.mission(mid)
        try:
            return sorted(f for f in os.listdir(mp.artifacts_dir(seat_id))
                          if f.startswith("round-"))
        except FileNotFoundError:
            return []

    def test_rounds_validation(self):
        with self.assertRaises(SystemExit):        # rounds on seats<2
            normalize_mission({"roles": [{"id": "x", "seats": 1, "rounds": 2}]})
        with self.assertRaises(SystemExit):        # rounds < 0
            normalize_mission({"roles": [{"id": "x", "seats": 2, "rounds": -1}]})
        _m, roles = normalize_mission({"roles": [{"id": "x", "seats": 2, "rounds": 3}]})
        self.assertTrue(all(r["rounds"] == 3 for r in roles if r.get("seat_group") == "x"))

    def test_round_barrier_blocks_until_peers_catch_up(self):
        from agent_amigos.runner import AmigoRunner
        mid = self.post(self._debate_spec(seats=3, rounds=3), mid="am-barrier")
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(),
                          {rid: {"node": "owner-node"} for rid in load_roles(mp)})
        r0 = AmigoRunner(self.bus, mid, "debater#0", "owner-node", agent_cli="stub")
        r0.turn_once()                             # 席0: round-0 を書く
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])
        r0.turn_once()                             # 他席が round-0 未 → バリアで待つ
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])
        # 他 2 席も round-0 を出すと、席0 が round-1 へ進める
        AmigoRunner(self.bus, mid, "debater#1", "owner-node", agent_cli="stub").turn_once()
        AmigoRunner(self.bus, mid, "debater#2", "owner-node", agent_cli="stub").turn_once()
        r0.turn_once()
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md", "round-1.md"])

    def test_debate_e2e_reaches_reviewing_with_all_rounds(self):
        mid = self.post(self._debate_spec(seats=3, rounds=3), mid="am-debate-e2e")
        d = NodeDaemon(self.bus, "owner-node", agent_cli="stub", interval=0)
        for _ in range(40):
            d.cycle()
            if self.phase(mid) == "reviewing":
                break
        self.assertEqual(self.phase(mid), "reviewing")
        for sid in ("debater#0", "debater#1", "debater#2"):
            self.assertEqual(self._round_files(mid, sid),
                             ["round-0.md", "round-1.md", "round-2.md"])
            mp = self.bus.mission(mid)
            self.assertTrue(os.path.isfile(os.path.join(mp.artifacts_dir(sid), "ANSWER.md")))

    def test_consensus_early_stop_finalizes_before_last_round(self):
        from agent_amigos.runner import AmigoRunner
        mid = self.post(self._debate_spec(seats=3, rounds=5, done_when="consensus",
                                          consensus_ratio=0.6, consensus_min=2),
                        mid="am-early")
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(),
                          {rid: {"node": "owner-node"} for rid in load_roles(mp)})
        # 全席が round-0 を同じ主張で出したと仮定（合意）
        for sid in ("debater#0", "debater#1", "debater#2"):
            p = os.path.join(mp.artifacts_dir(sid), "round-0.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("同じ主張")
        # 席0 のターン: round-1 を出す前に合意を検出して早期確定（ANSWER=round-0）
        AmigoRunner(self.bus, mid, "debater#0", "owner-node", agent_cli="stub").turn_once()
        self.assertEqual(self._round_files(mid, "debater#0"), ["round-0.md"])  # round-1 を作らない
        with open(os.path.join(mp.artifacts_dir("debater#0"), "ANSWER.md"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "同じ主張")


class ConductorTests(AmigosTestCase):
    """自律コンダクタ（オプトイン・G5 上位ループ）: 実行中に restaff で編成を調整する。"""

    def _post(self, conductor=None, roles=None):
        m = {"title": "t", "goal": "g", "staffing_timeout": 0,
             "convergence": {"done_when": "all-required-done", "quiescence_turns": 99}}
        if conductor is not None:
            m["conductor"] = conductor
        spec = {"mission": m, "roles": roles or
                [{"id": "worker", "mission": "作る", "deliverables": ["out.md"]},
                 {"id": "extra", "mission": "余剰", "required": False}]}
        return self.post(spec, mid="am-cond")

    def _mock_decision(self, decision):
        from agent_amigos import agentcli
        orig = agentcli.run_agent
        calls = []

        def fake(prompt, *a, **k):
            calls.append(prompt)
            return json.dumps(decision)
        agentcli.run_agent = fake
        self.addCleanup(setattr, agentcli, "run_agent", orig)
        return calls

    def test_conductor_disabled_is_skipped(self):
        from agent_amigos.ownerops import conductor_turn
        mid = self._post()                                  # conductor 無し
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude"),
                         "skipped")

    def test_conductor_applies_add_and_prune_then_round_gated(self):
        from agent_amigos.ownerops import conductor_turn
        from agent_amigos.mission import pruned_roles
        calls = self._mock_decision({"add": [{"id": "reviewer", "mission": "見る",
                                             "approver": True}], "prune": ["extra"], "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude"})
        mp = self.bus.mission(mid)
        mission = load_mission(mp)
        self.assertEqual(conductor_turn(self.bus, mp, mission, "owner-node", "claude"), "acted")
        self.assertIn("reviewer", load_roles(mp))
        self.assertIn("extra", pruned_roles(mp))
        n = len(calls)
        # 同一ラウンドの再評価はしない（LLM を毎サイクル呼ばない）
        self.assertEqual(conductor_turn(self.bus, mp, mission, "owner-node", "claude"), "idle")
        self.assertEqual(len(calls), n)

    def test_conductor_stub_is_noop(self):
        from agent_amigos.ownerops import conductor_turn
        mid = self._post(conductor={"enabled": True, "cli": "stub"})
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "stub"),
                         "idle")

    def test_conductor_guardrails_protect_core_roles(self):
        from agent_amigos.ownerops import conductor_turn
        from agent_amigos.mission import pruned_roles
        self._mock_decision({"add": [], "prune": ["integrator", "worker"], "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude"},
                         roles=[{"id": "worker", "mission": "w"}])   # 唯一の必須ワーカー
        mp = self.bus.mission(mid)
        conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude")
        self.assertNotIn("integrator", pruned_roles(mp))   # integrator は守る
        self.assertNotIn("worker", pruned_roles(mp))       # 最後の必須ワーカーは守る

    def test_conductor_respects_max_total_ops(self):
        from agent_amigos.ownerops import conductor_turn
        calls = self._mock_decision({"add": [{"id": "r", "mission": "m"}], "prune": [],
                                     "reason": "x"})
        mid = self._post(conductor={"enabled": True, "cli": "claude", "max_total_ops": 0})
        mp = self.bus.mission(mid)
        self.assertEqual(conductor_turn(self.bus, mp, load_mission(mp), "owner-node", "claude"),
                         "idle")
        self.assertEqual(len(calls), 0)                    # 上限で LLM を呼ぶ前に止まる


class AgentCliResolutionTests(AmigosTestCase):
    """使う agent CLI が決まらないときは stub へ落とさず paused にする（仕様書 §8）。

    stub は LLM なしのダミー応答なので、黙って既定にすると「# ANSWER.md / role: … 」
    のようなダミー成果物が統合・納品まで進む。壊れるなら観測できる形で壊す。
    """

    def _mid_without_role_cli(self):
        spec = base_spec()
        for r in spec["roles"]:
            r.pop("agent_cli", None)
        return self.post(spec)

    def test_missing_agent_cli_pauses_instead_of_stubbing(self):
        mid = self._mid_without_role_cli()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(), {"impl": {"node": "owner-node"}})
        r = AmigoRunner(self.bus, mid, "impl", "owner-node", agent_cli=None)
        self.assertEqual(r.turn_once(), "paused")
        st = read_json(mp.status("owner-node--impl"))
        self.assertEqual(st["state"], "paused")
        self.assertIn("agent-error:env", st["note"])
        # 成果物は 1 つも書かれない（ダミーが納品まで進まない）
        self.assertFalse(os.path.isdir(mp.artifacts_dir("impl")))
        # owner へ理由が届く
        self.assertTrue(any(m.get("subject") == "amigo paused"
                            for m in read_inbox(mp, "owner")))

    def test_role_agent_cli_is_enough(self):
        spec = base_spec()
        spec["roles"][1]["agent_cli"] = "stub"     # ロール側だけで決まればターンは走る
        mid = self.post(spec)
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(), {"impl": {"node": "owner-node"}})
        self.assertEqual(
            AmigoRunner(self.bus, mid, "impl", "owner-node", agent_cli=None).turn_once(),
            "acted")

    def test_owner_is_notified_once_not_every_turn(self):
        mid = self._mid_without_role_cli()
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(), {"impl": {"node": "owner-node"}})
        r = AmigoRunner(self.bus, mid, "impl", "owner-node", agent_cli=None)
        for _ in range(3):
            r.turn_once()
        notices = [m for m in read_inbox(mp, "owner") if m.get("subject") == "amigo paused"]
        self.assertEqual(len(notices), 1)


class AwayQuestionTimeoutTests(AmigosTestCase):
    """宛先が計画停止（away）中は question_timeout の時計を止める（仕様書 §3.3・§4）。

    止めないと、相手の PC が夜に落ちているだけで質問が期限切れになり、翌朝の owner の
    inbox が裁定要求で埋まる。相手は戻ると分かっているので待つのが正しい。
    """

    def _ask(self, mid, to="architect"):
        mp = self.bus.mission(mid)
        write_json_atomic(mp.roster(), {"impl": {"node": "owner-node"},
                                        to: {"node": "node-a"}})
        r = AmigoRunner(self.bus, mid, "impl", "owner-node", agent_cli="stub")
        r.turn_once()                       # stub は collaborates_with の先へ 1 度質問する
        st = read_json(mp.status("owner-node--impl"))
        self.assertTrue(st["open_questions"], "stub が質問を送っているはず")
        return mp, r, st

    def _make_away(self, mp, role, node, resume_in_sec):
        write_json_atomic(mp.status(f"{node}--{role}"),
                          {"node": node, "role": role, "state": "away",
                           "resume_at": time.strftime(
                               "%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(time.time() + resume_in_sec))})

    def test_open_question_records_addressee(self):
        mid = self.post()
        mp, _r, st = self._ask(mid)
        rec = next(iter(st["open_questions"].values()))
        self.assertEqual(rec["to"], "architect")

    def test_away_addressee_suppresses_escalation_and_informs_sender(self):
        mid = self.post()
        mp, r, _st = self._ask(mid)
        self._make_away(mp, "architect", "node-a", 3600)
        for _ in range(5):                  # question_timeout（既定 2）を十分に超えるまで
            r.turn_once()
        self.assertFalse([m for m in read_inbox(mp, "owner")
                          if str(m.get("subject", "")).startswith("未回答の質問")],
                         "away 中は owner へ昇格しない")
        # 送信側へ不在を 1 度だけ知らせる（毎ターン鳴らさない）
        notices = [m for m in read_inbox(mp, "impl")
                   if str(m.get("subject", "")).startswith("宛先 architect は不在")]
        self.assertEqual(len(notices), 1)

    def test_escalates_once_addressee_is_back(self):
        mid = self.post()
        mp, r, _st = self._ask(mid)
        self._make_away(mp, "architect", "node-a", 3600)
        for _ in range(3):
            r.turn_once()
        write_json_atomic(mp.status("node-a--architect"),
                          {"node": "node-a", "role": "architect", "state": "working"})
        for _ in range(4):                  # 復帰後はふつうに時計が進む
            r.turn_once()
        self.assertTrue([m for m in read_inbox(mp, "owner")
                         if str(m.get("subject", "")).startswith("未回答の質問")])
