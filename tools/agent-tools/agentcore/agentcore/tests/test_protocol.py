"""agentcore.protocol の単体テスト（claim/lease タイブレーク・延長・失効）。
実行: python3 -m unittest discover -s tools/agentcore/agentcore/tests
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agentcore import protocol  # noqa: E402
from agentcore import vocab  # noqa: E402
from agentcore import heartbeat  # noqa: E402


class TestClaimTieBreak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.claim_dir = os.path.join(self.tmp.name, "claims", "node-x")

    def test_first_claimant_wins(self):
        self.assertTrue(protocol.try_claim(self.claim_dir, "alice", 60))
        self.assertEqual(protocol.winner(self.claim_dir), "alice")

    def test_second_claimant_loses_while_lease_alive(self):
        self.assertTrue(protocol.try_claim(self.claim_dir, "alice", 60))
        self.assertFalse(protocol.try_claim(self.claim_dir, "bob", 60))
        self.assertEqual(protocol.winner(self.claim_dir), "alice")
        # 敗者は自分の claim ファイルを残さない
        self.assertNotIn("bob", protocol.list_claims(self.claim_dir))

    def test_deterministic_tiebreak_is_ts_then_who(self):
        # 手で ts を仕込み、(ts, who) の辞書式最小が勝つことを確認する
        os.makedirs(self.claim_dir, exist_ok=True)
        protocol.write_json_atomic(os.path.join(self.claim_dir, "z.json"), {
            "who": "z", "ts": 1.0, "lease_until": time.time() + 60})
        protocol.write_json_atomic(os.path.join(self.claim_dir, "a.json"), {
            "who": "a", "ts": 2.0, "lease_until": time.time() + 60})
        self.assertEqual(protocol.winner(self.claim_dir), "z")

    def test_malformed_claim_does_not_break_winner_selection(self):
        """壊れた 1 ファイル（lease_until/ts が数値でない）が比較例外で勝者判定そのものを
        止めると、そのロール/委譲は誰にも取れなくなる。無視して健全な claim から選ぶ。"""
        os.makedirs(self.claim_dir, exist_ok=True)
        protocol.write_json_atomic(os.path.join(self.claim_dir, "broken.json"), {
            "who": "broken", "ts": None, "lease_until": None})
        protocol.write_json_atomic(os.path.join(self.claim_dir, "junk.json"), {
            "who": "junk", "ts": "not-a-number", "lease_until": "soon"})
        protocol.write_json_atomic(os.path.join(self.claim_dir, "alice.json"), {
            "who": "alice", "ts": 1.0, "lease_until": time.time() + 60})
        self.assertEqual(protocol.winner(self.claim_dir), "alice")

    def test_claim_without_ts_never_wins(self):
        """`ts` 欠落を 0.0 と読むと、その claim が最小値として恒久的に勝ち続ける。

        lease は生きているので期限切れでも消えず、正当な claimant は全員 try_claim が
        False になる——「誰も取れない」ではなく「壊れた 1 件が独占する」失敗になる。"""
        os.makedirs(self.claim_dir, exist_ok=True)
        alice_ts = time.time()
        protocol.write_json_atomic(os.path.join(self.claim_dir, "alice.json"), {
            "who": "alice", "ts": alice_ts, "lease_until": time.time() + 60})
        for name, rec in (("no-ts", {"who": "no-ts", "lease_until": time.time() + 60}),
                          ("null-ts", {"who": "null-ts", "ts": None,
                                       "lease_until": time.time() + 60})):
            with self.subTest(name=name):
                protocol.write_json_atomic(os.path.join(self.claim_dir, f"{name}.json"), rec)
                self.assertEqual(protocol.winner(self.claim_dir), "alice")

    def test_non_finite_ts_is_ignored(self):
        """NaN は比較が常に False になるため、`min()` の結果が入力順で変わる。
        決定性（同じ claim 集合なら全ノードが同じ勝者を選ぶ）を守るため無視する。"""
        os.makedirs(self.claim_dir, exist_ok=True)
        protocol.write_json_atomic(os.path.join(self.claim_dir, "nan.json"), {
            "who": "nan", "ts": float("nan"), "lease_until": time.time() + 60})
        protocol.write_json_atomic(os.path.join(self.claim_dir, "bob.json"), {
            "who": "bob", "ts": time.time(), "lease_until": time.time() + 60})
        self.assertEqual(protocol.winner(self.claim_dir), "bob")

    def test_claim_after_lease_expiry_can_be_won_by_new_claimant(self):
        os.makedirs(self.claim_dir, exist_ok=True)
        protocol.write_json_atomic(os.path.join(self.claim_dir, "alice.json"), {
            "who": "alice", "ts": 1.0, "lease_until": time.time() - 5})  # 失効済み
        self.assertTrue(protocol.try_claim(self.claim_dir, "bob", 60))
        self.assertEqual(protocol.winner(self.claim_dir), "bob")

    def test_sync_callbacks_invoked_in_order(self):
        events = []
        ok = protocol.try_claim(
            self.claim_dir, "alice", 60,
            on_write=lambda: events.append("write"),
            on_sync=lambda: events.append("sync"))
        self.assertTrue(ok)
        self.assertEqual(events, ["write", "sync"])

    def test_on_withdraw_called_only_when_race_lost_after_sync(self):
        """on_sync（pull 相当）で他ノードのより小さい ts の claim が後から取り込まれた
        （git 分散の並行 claim）ケースだけ on_withdraw が呼ばれる。"""
        withdraw_calls = []

        def _simulate_incoming_winner():
            protocol.write_json_atomic(os.path.join(self.claim_dir, "alice.json"), {
                "who": "alice", "ts": 0.0, "lease_until": time.time() + 60})

        ok = protocol.try_claim(
            self.claim_dir, "bob", 60,
            on_sync=_simulate_incoming_winner,
            on_withdraw=lambda: withdraw_calls.append("bob"))
        self.assertFalse(ok)
        self.assertEqual(withdraw_calls, ["bob"])
        self.assertNotIn("bob", protocol.list_claims(self.claim_dir))

    def test_no_withdraw_call_when_blocked_before_writing(self):
        """既に他者が lease 内の勝者なら、自分は書き込みすらしない
        （withdraw する対象が無いので on_withdraw も呼ばれない）。"""
        withdraw_calls = []
        protocol.try_claim(self.claim_dir, "alice", 60)
        ok = protocol.try_claim(
            self.claim_dir, "bob", 60,
            on_withdraw=lambda: withdraw_calls.append("bob"))
        self.assertFalse(ok)
        self.assertEqual(withdraw_calls, [])

    def test_extra_fields_are_preserved(self):
        protocol.try_claim(self.claim_dir, "alice", 60, extra={"workload": "amigos"})
        self.assertEqual(protocol.list_claims(self.claim_dir)["alice"]["workload"], "amigos")

    def test_extra_cannot_overwrite_claim_invariants(self):
        protocol.write_claim(self.claim_dir, "alice", 60,
                             extra={"who": "bob", "lease_until": 0, "workload": "x"})
        rec = protocol.list_claims(self.claim_dir)["alice"]
        self.assertEqual(rec["who"], "alice")
        self.assertGreater(rec["lease_until"], time.time())
        self.assertEqual(rec["workload"], "x")


class TestExtendAndRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.claim_dir = os.path.join(self.tmp.name, "claims", "node-x")

    def test_extend_claim_keeps_ts_but_bumps_lease(self):
        protocol.write_claim(self.claim_dir, "alice", 60)
        original_ts = protocol.list_claims(self.claim_dir)["alice"]["ts"]
        self.assertTrue(protocol.extend_claim(self.claim_dir, "alice", 120))
        rec = protocol.list_claims(self.claim_dir)["alice"]
        self.assertEqual(rec["ts"], original_ts)
        self.assertGreater(rec["lease_until"], time.time() + 100)

    def test_extend_claim_fails_if_claim_missing(self):
        self.assertFalse(protocol.extend_claim(self.claim_dir, "alice", 60))

    def test_extend_claim_fails_if_lost_to_other_winner(self):
        os.makedirs(self.claim_dir, exist_ok=True)
        protocol.write_json_atomic(os.path.join(self.claim_dir, "alice.json"), {
            "who": "alice", "ts": 5.0, "lease_until": time.time() - 1})  # 失効
        protocol.write_json_atomic(os.path.join(self.claim_dir, "bob.json"), {
            "who": "bob", "ts": 1.0, "lease_until": time.time() + 60})  # 生存・より小さい ts
        self.assertFalse(protocol.extend_claim(self.claim_dir, "alice", 60))

    def test_release_claim_removes_file(self):
        protocol.write_claim(self.claim_dir, "alice", 60)
        protocol.release_claim(self.claim_dir, "alice")
        self.assertEqual(protocol.list_claims(self.claim_dir), {})

    def test_release_claim_is_idempotent(self):
        protocol.release_claim(self.claim_dir, "nobody")  # 例外を投げない


class TestRenewLease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.claim_dir = os.path.join(self.tmp.name, "bids")

    def test_new_bid_is_created(self):
        self.assertTrue(protocol.renew_lease(self.claim_dir, "alice", 900, extra={"workload": "amigos"}))
        rec = protocol.list_claims(self.claim_dir)["alice"]
        self.assertEqual(rec["workload"], "amigos")

    def test_renew_skipped_when_still_fresh(self):
        protocol.renew_lease(self.claim_dir, "alice", 900)
        self.assertFalse(protocol.renew_lease(self.claim_dir, "alice", 900))

    def test_renew_happens_past_half_life_and_preserves_ts(self):
        path = os.path.join(self.claim_dir, "alice.json")
        protocol.write_json_atomic(path, {
            "who": "alice", "ts": 42.0, "claimed_at": "2026-01-01T00:00:00Z",
            "lease_until": time.time() + 10})  # 残り 10s < lease/2(450s) → 更新対象
        self.assertTrue(protocol.renew_lease(self.claim_dir, "alice", 900))
        rec = protocol.list_claims(self.claim_dir)["alice"]
        self.assertEqual(rec["ts"], 42.0)
        self.assertEqual(rec["claimed_at"], "2026-01-01T00:00:00Z")
        self.assertGreater(rec["lease_until"], time.time() + 800)


    def test_renew_does_not_create_when_create_if_missing_false(self):
        """心拍用: 手放した／取り下げられた claim を書き戻さない
        （書き戻すと誰も動いていないロールを占有し続ける zombie 勝者になる）。"""
        self.assertFalse(protocol.renew_lease(self.claim_dir, "alice", 900,
                                              create_if_missing=False))
        self.assertEqual(protocol.list_claims(self.claim_dir), {})
        # 既にある自分の claim は（半減期を過ぎていれば）延長する
        path = os.path.join(self.claim_dir, "alice.json")
        protocol.write_json_atomic(path, {
            "who": "alice", "ts": 42.0, "lease_until": time.time() + 10})
        self.assertTrue(protocol.renew_lease(self.claim_dir, "alice", 900,
                                             create_if_missing=False))
        self.assertEqual(protocol.list_claims(self.claim_dir)["alice"]["ts"], 42.0)

    def test_renew_tolerates_malformed_lease_until(self):
        path = os.path.join(self.claim_dir, "alice.json")
        protocol.write_json_atomic(path, {
            "who": "alice", "ts": 42.0, "lease_until": "bad"})
        self.assertTrue(protocol.renew_lease(self.claim_dir, "alice", 900,
                                             create_if_missing=False))
        rec = protocol.list_claims(self.claim_dir)["alice"]
        self.assertEqual(rec["ts"], 42.0)
        self.assertIsInstance(rec["lease_until"], float)


class TestVocab(unittest.TestCase):
    def test_terminal_constants(self):
        self.assertEqual(vocab.TERMINAL, frozenset({"done", "failed", "cancelled"}))
        self.assertTrue(vocab.is_terminal("cancelled"))
        self.assertTrue(vocab.is_terminal("done"))
        self.assertFalse(vocab.is_terminal("canceled"))  # 米式は書く語彙に含めない
        self.assertFalse(vocab.is_terminal("working"))
        self.assertFalse(vocab.is_terminal(None))

    def test_read_side_accepts_legacy_spelling(self):
        """既存データの読み取りだけは旧綴りも終端と認める。認めないと、改称前に
        cancel された run が非終端＝実行中に見え、孤児回収で蘇る。"""
        self.assertEqual(vocab.TERMINAL_READ, frozenset({"done", "failed", "cancelled", "canceled"}))
        self.assertTrue(vocab.is_terminal_read("canceled"))
        self.assertTrue(vocab.is_terminal_read("cancelled"))
        self.assertFalse(vocab.is_terminal_read("running"))
        self.assertFalse(vocab.is_terminal_read(None))


class TestHeartbeat(unittest.TestCase):
    def test_is_fresh_within_window(self):
        rec = {"heartbeat": heartbeat.now_iso()}
        self.assertTrue(heartbeat.is_fresh(rec, 60))

    def test_is_fresh_false_when_stale(self):
        old = time.time() - 1000
        rec = {"heartbeat": heartbeat.now_iso()}
        self.assertFalse(heartbeat.is_fresh(rec, 60, now=time.time() + 1000))
        self.assertFalse(heartbeat.is_fresh(None, 60))
        self.assertFalse(heartbeat.is_fresh({}, 60))

    def test_is_lease_alive(self):
        self.assertTrue(heartbeat.is_lease_alive({"lease_until": time.time() + 10}))
        self.assertFalse(heartbeat.is_lease_alive({"lease_until": time.time() - 10}))
        self.assertFalse(heartbeat.is_lease_alive(None))
        self.assertFalse(heartbeat.is_lease_alive({}))


if __name__ == "__main__":
    unittest.main()
