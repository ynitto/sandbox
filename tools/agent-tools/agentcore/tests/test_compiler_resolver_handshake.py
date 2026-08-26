"""M3 handshake — 実 Compiler（JS）の出力を実 Resolver（Python）が読む直結検証。

段A の schema examples を挟んだ間接の噛み合わせは双方の契約テストが縛るが、
「実装同士」の直結はここが唯一の網。Compiler の出力形が変わって Resolver が
読めなくなる回帰（例: 空 candidates・status 語彙のずれ）を、リリース前に落とす。
node が無い環境ではスキップする（欠けた木の数字として読まないため名前で分かる）。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import unittest

from agentcore.executioncontract import selection_policy_errors
from agentcore.executionresolver import resolve_execution

REPO = pathlib.Path(__file__).parents[4]
COMPILER = REPO / "tools/agent-dashboard/src/features/orchestration/main/execution-policy-compiler.js"
NODE = shutil.which("node")

_SCRIPT = """
const compiler = require(%(compiler)s);
function qualification(id, status, lower, cost, p50) {
  return { qualification_id: id, status, samples: 9, passed: Math.round(lower * 9),
    success_rate_lower_bound: lower, critical_failure_risk: status === 'blocked' ? 1 : 0,
    p50_seconds: p50, expected_calls_with_failures: lower > 0 ? 1 / lower : null,
    last_evaluated_at: '2026-08-14T00:00:00Z', valid_until: '2026-11-12T00:00:00Z',
    source: 'eval-archive', economics: { estimated_cost: cost } };
}
// 処理種別の鍵は実カタログ（purpose-operations.js）の語彙で書く——by_purpose の
// 直結を見るのがこのテストの目的なので、鍵がカタログと噛み合わないと全部空になる。
const qualifications = { version: 1, revision: 12, candidates: [
  { agent_cli: 'aider', model: 'gemma4:e4b', economics: { estimated_cost: 0 },
    qualifications: { 'existing-test-repair':
      qualification('aider-repair-v1', 'qualified', 0.72, 0, 141) } },
  { agent_cli: 'cursor', model: 'grok-4.5', economics: { estimated_cost: 1 },
    qualifications: { 'bounded-analysis':
      qualification('cursor-general-v1', 'qualified', 0.9, 1, 30) } },
  { agent_cli: 'ollama', model: 'gemma4:12b', economics: { estimated_cost: 0 },
    qualifications: { 'bounded-review':
      qualification('ollama-12b-review-v1', 'trial', 0.5, 0, 60) } },
  { agent_cli: 'aider', model: 'gemma4:12b', economics: { estimated_cost: 0 },
    qualifications: { 'code-worker': qualification('aider-12b-code-v1', 'blocked', 0, 0, 600) } },
]};
const tiers = { basic: { order: 0, candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }] },
                small: { order: 1, candidates: [{ agent_cli: 'ollama', model: 'gemma4:12b' },
                                                { agent_cli: 'aider', model: 'gemma4:12b' }] },
                medium: { order: 2, candidates: [{ agent_cli: 'cursor', model: 'grok-4.5' }] } };
const policy = compiler.compileSelectionPolicy({ strategy: 'economy', tiers,
  tierCeiling: 'medium', qualifications, nowMs: Date.parse('2026-08-15T00:00:00Z') });
console.log(JSON.stringify({ version: 2, revision: 7, workloads: { flow: {
  agent_cli: 'aider', model: 'gemma4:e4b', tier: 'medium',
  selection_policy: policy } } }));
"""


@unittest.skipUnless(NODE and COMPILER.is_file(), "node または Compiler が無い環境ではスキップ")
class CompilerResolverHandshakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = _SCRIPT % {"compiler": json.dumps(str(COMPILER))}
        proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr[:500]
        cls.control = json.loads(proc.stdout)
        cls.policy = cls.control["workloads"]["flow"]["selection_policy"]

    def test_compiler_output_passes_contract(self):
        self.assertEqual(selection_policy_errors(self.policy), [])
        ids = [(c["agent_cli"], c["model"]) for c in self.policy["candidates"]]
        self.assertIn(("aider", "gemma4:e4b"), ids)
        self.assertNotIn(("aider", "gemma4:12b"), ids, "blocked 候補は policy に載らない")

    def test_trial_only_candidate_is_marked(self):
        trial = next(c for c in self.policy["candidates"]
                     if (c["agent_cli"], c["model"]) == ("ollama", "gemma4:12b"))
        self.assertEqual(trial.get("status"), "trial")
        qualified = next(c for c in self.policy["candidates"]
                         if (c["agent_cli"], c["model"]) == ("aider", "gemma4:e4b"))
        self.assertNotIn("status", qualified)

    def test_resolver_auto_selects_economy_rank1_and_skips_trial(self):
        decision = resolve_execution("flow", compiled_control=self.control)
        self.assertEqual(decision["selected"], {"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertEqual(decision["selection_source"], "qualified-candidate")
        fallbacks = [tuple(f.values()) for f in decision["fallback_candidates"]]
        self.assertNotIn(("ollama", "gemma4:12b"), fallbacks,
                         "trial 候補は通常 run の候補列に入らない")

    def test_by_purpose_passes_contract_and_reaches_the_resolver(self):
        """Compiler が焼いた用途別の順位表を、Resolver がそのまま読めること。

        ここが繋がっていないと自動選択は workload ごとの 1 位が全ノードを取る形へ
        戻り、抽出の実績しかない候補がレビューにも選ばれる。
        """
        by_purpose = self.policy.get("by_purpose")
        self.assertIsInstance(by_purpose, dict, "Compiler が by_purpose を出していない")
        self.assertEqual(selection_policy_errors(self.policy), [])

        # work は existing-test-repair の裏付けを持つ aider だけ。
        work = resolve_execution("flow", compiled_control=self.control,
                                 purpose_or_role="work")
        self.assertEqual(work["selected"], {"agent_cli": "aider", "model": "gemma4:e4b"})
        self.assertEqual(work["purpose"], "work")

        # filter は bounded-analysis の裏付けを持つ cursor だけ——workload 共通の
        # 順位表（economy の 1 位 = aider/e4b）とは違う候補が選ばれる。
        filtered = resolve_execution("flow", compiled_control=self.control,
                                     purpose_or_role="filter")
        self.assertEqual(filtered["selected"], {"agent_cli": "cursor", "model": "grok-4.5"})

    def test_by_purpose_keeps_trial_gating_and_blocked_exclusion(self):
        by_purpose = self.policy["by_purpose"]
        review = by_purpose["verify"]["candidates"]
        self.assertEqual([(c["agent_cli"], c["model"]) for c in review],
                         [("ollama", "gemma4:12b")])
        self.assertEqual(review[0].get("status"), "trial")
        # trial だけの用途は通常 run で自動選択されない（明示承認 run 限定）。
        decision = resolve_execution("flow", compiled_control=self.control,
                                     purpose_or_role="verify")
        self.assertIsNone(decision["selected"])
        self.assertEqual(decision["park_reason"], "no-eligible-candidate")
        # blocked の候補はどの用途にも現れない。
        for entry in by_purpose.values():
            for candidate in entry["candidates"]:
                self.assertNotEqual((candidate["agent_cli"], candidate["model"]),
                                    ("aider", "gemma4:12b"))

    def test_purpose_without_evidence_parks_instead_of_borrowing(self):
        """planner は裏付けが無い＝空宣言。共通の順位表へ倒れない。"""
        self.assertEqual(self.policy["by_purpose"]["planner"]["candidates"], [])
        decision = resolve_execution("flow", compiled_control=self.control,
                                     purpose_or_role="planner")
        self.assertIsNone(decision["selected"])
        self.assertEqual(decision["park_reason"], "no-qualified-candidate-for-purpose")

    def test_unknown_purpose_still_uses_the_flat_list(self):
        """カタログに無い用途は従来どおり（additive であること）。"""
        decision = resolve_execution("flow", compiled_control=self.control,
                                     purpose_or_role="not-in-catalog")
        self.assertEqual(decision["selected"], {"agent_cli": "aider", "model": "gemma4:e4b"})

    def test_trial_candidate_runs_only_with_envelope_approval(self):
        pin = {"agent_cli": "ollama", "model": "gemma4:12b"}
        parked = resolve_execution("flow", compiled_control=self.control, explicit_pin=pin)
        self.assertEqual(parked["park_reason"], "pin-not-qualified")
        approved = resolve_execution("flow", compiled_control=self.control,
                                     explicit_pin={**pin, "trial_approved": True})
        self.assertEqual(approved["selection_source"], "trial-candidate")


if __name__ == "__main__":
    unittest.main()
