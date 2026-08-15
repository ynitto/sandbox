"""agentcore.board の単体テスト（入札選別規則の 1 実装）。

    python -m unittest discover -s tools/agent-tools/agentcore/tests

移植元は agent-flow / agent-amigos の `board_eligible`（同じ仕様・別実装だったもの）。
両者の既存テストが通る判定であることに加え、契約（board.schema.json）にあって
どちらも見ていなかった `requires.agent_cli` / `requires.contract_version` を確かめる。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import board  # noqa: E402
from agentcore.protocol import safe_name  # noqa: E402

REGISTRY = {
    "_meta": {"generated_from": "charter"},
    "app": {"url": "https://git.example.com/team/app.git", "owns": ["src/**"]},
    "docs": {"url": "https://git.example.com/team/docs.git", "readonly": True},
    "ref": {"url": "https://git.example.com/team/ref.git"},          # owns 無し＝参照
}

HOST_REPOS = [
    {"url": "https://git.example.com/team/app.git", "local": "/home/me/mirrors/app"},
    {"url": "https://git.example.com/team/docs.git", "readonly": True},
]


def post(**kw):
    return {"op": "post", "id": "dg-1", "workload": "flow", **kw}


class DeclaredRepoTests(unittest.TestCase):
    def test_registry_form_drops_readonly_and_reference(self):
        have = board.declared_repo_ids(REGISTRY)
        self.assertIn("app", have)
        self.assertIn(board.normalize_repo_url(REGISTRY["app"]["url"]), have)
        self.assertNotIn("docs", have)      # readonly は書込先候補にしない
        self.assertNotIn("ref", have)       # owns 無し＝参照リポジトリ
        self.assertNotIn("_meta", have)     # メタデータ予約

    def test_host_repos_form_keeps_entries_without_owns(self):
        """host.yaml の `repos[]` は「手元にクローンがある」宣言で `owns` を持たない。
        レジストリと同じ条件で絞ると 1 件も残らず、常駐体が何にも入札できなくなる。"""
        have = board.declared_repo_ids(HOST_REPOS)
        self.assertIn(board.normalize_repo_url(HOST_REPOS[0]["url"]), have)
        self.assertNotIn(board.normalize_repo_url(HOST_REPOS[1]["url"]), have)   # readonly は除く

    def test_plain_url_list(self):
        have = board.declared_repo_ids(["https://git.example.com/team/app.git"])
        self.assertIn(board.normalize_repo_url("https://git.example.com/team/app"), have)


class EligibleTests(unittest.TestCase):
    def test_workspace_url_must_be_declared(self):
        p = post(workspace={"url": "https://git.example.com/team/app.git"})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        self.assertTrue(board.eligible(p, repos=HOST_REPOS))
        other = post(workspace={"url": "https://git.example.com/team/other.git"})
        self.assertFalse(board.eligible(other, repos=REGISTRY))

    def test_url_matching_absorbs_git_suffix_and_case(self):
        p = post(workspace={"url": "HTTPS://git.example.com/team/APP/"})
        self.assertTrue(board.eligible(p, repos=REGISTRY))

    def test_no_workspace_is_eligible(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY))

    def test_tags_must_be_subset(self):
        p = post(requires={"tags": ["python", "gpu"]})
        self.assertFalse(board.eligible(p, repos=REGISTRY, tags=["python"]))
        self.assertTrue(board.eligible(p, repos=REGISTRY, tags=["python", "gpu", "x"]))

    def test_requires_repos_all_must_be_declared(self):
        p = post(requires={"repos": ["https://git.example.com/team/app.git"]})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        p2 = post(requires={"repos": ["https://git.example.com/team/app.git",
                                      "https://git.example.com/team/docs.git"]})
        self.assertFalse(board.eligible(p2, repos=REGISTRY))   # docs は readonly

    def test_agent_cli_is_or(self):
        p = post(requires={"agent_cli": ["codex", "claude"]})
        self.assertTrue(board.eligible(p, repos=REGISTRY, agent_cli=["claude"]))
        self.assertFalse(board.eligible(p, repos=REGISTRY, agent_cli=["kiro"]))

    def test_agent_cli_undeclared_node_does_not_bid(self):
        """CLI 指定つきの公示を、使える CLI を宣言していないノードが拾う理由が無い。"""
        p = post(requires={"agent_cli": ["codex"]})
        self.assertFalse(board.eligible(p, repos=REGISTRY, agent_cli=[]))
        self.assertTrue(board.eligible(post(), repos=REGISTRY, agent_cli=[]))   # 要求が無ければ不問

    def test_contract_version_fail_close(self):
        p = post(requires={"contract_version": board.CONTRACT_VERSION})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        self.assertFalse(board.eligible(p, repos=REGISTRY, contract_version=None))
        self.assertFalse(board.eligible(p, repos=REGISTRY,
                                        contract_version=board.CONTRACT_VERSION + 1))

    def test_contract_version_absent_is_unrestricted(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, contract_version=None))

    def test_broken_post_is_not_eligible(self):
        self.assertFalse(board.eligible(None, repos=REGISTRY))
        # 壊れた requires を「制限なし」に倒すと、本来拾えない公示を誤って拾う。
        self.assertFalse(board.eligible(post(requires="こわれている"), repos=REGISTRY))

    def test_string_contract_version_is_parsed(self):
        p = post(requires={"contract_version": str(board.CONTRACT_VERSION)})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        self.assertFalse(board.eligible(
            post(requires={"contract_version": "999"}), repos=REGISTRY))

    def test_unparseable_contract_version_fails_closed(self):
        self.assertFalse(board.eligible(
            post(requires={"contract_version": "not-a-number"}), repos=REGISTRY))

    def test_non_finite_contract_version_fails_closed_without_raising(self):
        """NaN / Inf は「拾わない」であって「落ちる」ではない。

        `json` は既定で `NaN` / `Infinity` リテラルを受理するので、そういう公示が板に
        1 件混ざると `int()` の ValueError / OverflowError が入札巡回ごと止めてしまう
        （fail-close を入れた際の取りこぼし）。"""
        for raw in ('{"contract_version": NaN}', '{"contract_version": Infinity}',
                    '{"contract_version": -Infinity}'):
            req = json.loads(raw)
            with self.subTest(raw=raw):
                self.assertFalse(board.eligible(post(requires=req), repos=REGISTRY))

    def test_fractional_contract_version_fails_closed(self):
        self.assertFalse(board.eligible(
            post(requires={"contract_version": 1.5}), repos=REGISTRY))


class WorkloadEligibilityTests(unittest.TestCase):
    """`workloads` は**ノードが「これしかやらない」と言う条件**なので fail-open（P2-3）。

    `tags` / `agent_cli` / `contract_version`（公示が「要る」と言う条件）とは判定の向きが
    逆になる——要求を無視すると拾えないノードが拾い、制限を強制すると宣言していない
    ノードが全部止まる。安全な倒し方が逆。"""

    def test_undeclared_workloads_do_not_restrict(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY))
        self.assertTrue(board.eligible(post(), repos=REGISTRY, workloads=[]))
        self.assertTrue(board.eligible(post(), repos=REGISTRY, workloads=None))

    def test_declared_workloads_restrict(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, workloads=["flow"]))
        self.assertFalse(board.eligible(post(), repos=REGISTRY, workloads=["amigos"]))
        self.assertTrue(board.eligible(post(workload="amigos"), repos=REGISTRY,
                                       workloads=["flow", "amigos"]))

    def test_declared_workloads_reject_a_post_without_workload(self):
        # workload の無い公示を「これしかやらない」と宣言したノードが拾う根拠が無い
        p = post()
        p.pop("workload")
        self.assertFalse(board.eligible(p, repos=REGISTRY, workloads=["flow"]))

    def test_declared_workloads_reads_only_explicit_declarations(self):
        self.assertEqual(board.declared_workloads({}), [])
        self.assertEqual(board.declared_workloads({"amigos_bus": "/tmp/bus"}), [],
                         "設定から導出しない（宣言していない PC を黙って止めない）")
        self.assertEqual(board.declared_workloads({"workloads": ["flow", "amigos"]}),
                         ["flow", "amigos"])
        self.assertEqual(board.declared_workloads({"workloads": "flow"}), ["flow"],
                         "スカラは 1 要素へ畳む（1 文字ずつに分解しない）")
        self.assertEqual(board.declared_workloads({"workloads": 3}), [])


class MaxConcurrentEligibilityTests(unittest.TestCase):
    """「超過時は新規入札を控える」という板の契約（`$defs.node.max_concurrent`）の実装。"""

    def test_zero_and_none_are_unlimited(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, max_concurrent=0, inflight=99))
        self.assertTrue(board.eligible(post(), repos=REGISTRY, max_concurrent=None, inflight=99))

    def test_busy_node_stops_bidding(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, max_concurrent=2, inflight=1))
        self.assertFalse(board.eligible(post(), repos=REGISTRY, max_concurrent=2, inflight=2))
        self.assertFalse(board.eligible(post(), repos=REGISTRY, max_concurrent=2, inflight=3))

    def test_broken_declaration_is_unlimited(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, max_concurrent="たくさん",
                                       inflight=99))


class NodeInflightTests(unittest.TestCase):
    """板上の「自分がいま預かっている件数」（`max_concurrent` 自己抑制の材料）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agentcore-board-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _deleg(self, did, *, state=None, who="pc-a", terminal_mark=None):
        d = self.tmp / "delegations" / did
        (d / "status").mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text("{}", encoding="utf-8")
        if state is not None:
            (d / "status" / f"{safe_name(who)}.json").write_text(
                json.dumps({"who": who, "state": state}), encoding="utf-8")
        if terminal_mark:
            (d / terminal_mark).write_text("{}", encoding="utf-8")
        return d

    def test_counts_only_my_unfinished(self):
        self._deleg("a", state="dispatched")
        self._deleg("b", state="working")
        self._deleg("c", state="done")                       # 終端
        self._deleg("d", state="working", who="pc-b")        # 他ノード名義
        self._deleg("e")                                     # 誰も引き受けていない
        self.assertEqual(board.node_inflight(str(self.tmp), "pc-a"), 2)

    def test_legacy_cancelled_spelling_is_terminal(self):
        """板には語彙統一（W0-9）より前のノードが書いた `canceled`（米式）が残りうる。
        読みは寛容（`is_terminal_read`）——終端を「実行中」と読むと枠が永久に空かない。"""
        self._deleg("a", state="canceled")
        self.assertEqual(board.node_inflight(str(self.tmp), "pc-a"), 0)

    def test_away_still_occupies_a_slot(self):
        # 計画停止中は終端ではない——枠は空いていない
        self._deleg("a", state="away")
        self.assertEqual(board.node_inflight(str(self.tmp), "pc-a"), 1)

    def test_terminal_delegation_frees_the_slot_even_with_a_stale_status(self):
        self._deleg("a", state="working", terminal_mark="result.json")
        self._deleg("b", state="working", terminal_mark="cancelled.json")
        self.assertEqual(board.node_inflight(str(self.tmp), "pc-a"), 0)

    def test_missing_board_is_zero(self):
        self.assertEqual(board.node_inflight(str(self.tmp / "nope"), "pc-a"), 0)

    def test_holds_delegation_matches_the_bid_naming_rule(self):
        """名義の綴りは入札を書く `protocol.renew_lease`（`safe_name`）と同じ規則。
        ここが別実装だと、書いたファイルと読むファイルが別名になる（P2-5）。"""
        self._deleg("a", state="working", who="pc a/b")
        self.assertTrue(board.holds_delegation(str(self.tmp), "a", "pc a/b"))


class ContractCompatibleTests(unittest.TestCase):
    def test_no_requirement_is_unrestricted(self):
        self.assertTrue(board.contract_compatible(None, declared=None))

    def test_undeclared_node_fails_close(self):
        self.assertFalse(board.contract_compatible(1, declared=None))

    def test_exact_match(self):
        self.assertTrue(board.contract_compatible(1, declared=1))
        self.assertFalse(board.contract_compatible(1, declared=2))


class BudgetEligibilityTests(unittest.TestCase):
    """can_accept + freshness（status_budget_gate と単一実装）。budget 省略は従来互換。"""

    def _budget(self, can_accept=True, reason_codes=None, enforce=False,
                contract_version=1, source="local-ledger"):
        return {
            "contract_version": contract_version,
            "observed_at": "2026-08-01T12:00:00Z",
            "source": source,
            "capacity": {"limit": 1, "used": 0 if can_accept else 1, "reserved": None},
            "can_accept": can_accept,
            "reason_codes": list(reason_codes or (["ok"] if can_accept else ["exceeded"])),
            "enforce": enforce,
        }

    def test_budget_omitted_keeps_legacy_eligible(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY))

    def test_enforce_false_exhausted_remains_eligible(self):
        self.assertTrue(board.eligible(
            post(), repos=REGISTRY, budget=self._budget(can_accept=False, enforce=False),
            heartbeat="2026-08-01T12:00:00Z", fresh_after_sec=3600,
            at=__import__("datetime").datetime(2026, 8, 1, 12, 1, tzinfo=__import__("datetime").timezone.utc)))

    def test_enforce_true_exhausted_not_eligible(self):
        self.assertFalse(board.eligible(
            post(), repos=REGISTRY, budget=self._budget(can_accept=False, enforce=True),
            heartbeat="2026-08-01T12:00:00Z", fresh_after_sec=3600,
            at=__import__("datetime").datetime(2026, 8, 1, 12, 1, tzinfo=__import__("datetime").timezone.utc)))

    def test_version_mismatch_is_unknown_not_eligible_when_enforce(self):
        from datetime import datetime, timezone
        at = datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc)
        self.assertFalse(board.eligible(
            post(), repos=REGISTRY,
            budget=self._budget(can_accept=True, enforce=True, contract_version=99),
            heartbeat="2026-08-01T12:00:00Z", fresh_after_sec=3600, at=at))
        gate = board.status_budget_gate(
            {"node": "pc-a", "availability": "active",
             "updated_iso": "2026-08-01T12:00:00Z", "fresh_after_sec": 3600,
             "budget": self._budget(can_accept=True, enforce=True, contract_version=99)},
            at=at)
        self.assertEqual(gate["kind"], "unknown")
        self.assertFalse(gate["eligible"])

    def test_budget_kwargs_from_node_empty_without_budget(self):
        self.assertEqual(board.budget_kwargs_from_node({"node": "pc-a"}), {})
        self.assertEqual(board.budget_kwargs_from_node(None), {})

    def test_budget_kwargs_from_node_maps_heartbeat(self):
        kw = board.budget_kwargs_from_node({
            "node": "pc-a", "heartbeat": "2026-08-01T12:00:00Z",
            "fresh_after_sec": 90, "budget": self._budget()})
        self.assertEqual(kw["heartbeat"], "2026-08-01T12:00:00Z")
        self.assertEqual(kw["fresh_after_sec"], 90)
        self.assertIn("budget", kw)


if __name__ == "__main__":
    unittest.main()
