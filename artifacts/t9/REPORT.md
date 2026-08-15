# t9 — Phase2 reservation（claim CAS / award / close / expiry）

## (a) 成果

Phase2 予算 reservation を additive 実装。排他は既存 `state_transaction` CAS のみ。タイブレーク `(load, name)` 未変更。

| 変更 | 内容 |
|------|------|
| `coordination.py` | `create_reservation_in_root` / close / expire。`claim_distributed_task` が同一 CAS で `reservations/<rsv-…>.json` 作成。不変条件 `used+live≤limit`（unlimited スキップ、unknown は予約しない） |
| `batch.py` | 単独ノード claim でも予約。`release_claim` で close（`offloaded` は維持）。`recover_stale_doing` で expiry |
| `loop.py` | `write_status` が `capacity.reserved` = live 合計（所有者ノードのみ） |
| `board.py` | `write_award` と同契約で板 `reservations/` 作成 |
| `prioritize.py` / `agent_flow/agent.py` | ledger 記帳に `AGENT_RESERVATION_ID` を引き継ぎ |
| schema | `reserved` 説明を Phase2 埋込に更新 |
| tests | `TestBudgetReservation`（5） |

**完了条件との対応**

| 条件 | 結果 |
|------|------|
| claim と同時に reserved（CAS） | 達した |
| award と同契約 | 達した |
| 開始時引継ぎ + ledger close | 達した（env + release/settle close） |
| 未開始/停止は expiry 冪等解放 | 達した |
| used+live≤limit（unlimited/unknown 別値） | 達した |
| 第二排他・残量連続優先度・中央スケジューラなし | 守った |

## (b) 検証

| ID | コマンド | 結果 |
|----|---------|------|
| V1 | `unittest TestBudgetReservation` | **5 ok** |
| V2 | `TestAllocateClaimBudgetGate` + `TestStatusBudgetGate` 含む `tests.test_coordination` | **52 ok** |
| V3 | `tests.test_node_budget_summary` | **6 ok**（reserved=0.0） |
| V4 | `TestDoctorCreditKnowledge` | **3 ok**（読取契約互換） |
| V5 | `tests.test_contract_baseline` | **14 ok** |

## (c) 前提・未解決・範囲外

**採用した前提**

1. synth1 §3: 作成点は claim/award 同時。排他は `state_transaction` のみ。`reason_codes` に予約語彙を足さない（journal `reservation:` 行）。
2. 予約量既定は `budget_summary.reservation_amount`、無ければ容量単位で `1.0`。unlimited は amount=0。headroom 無し（degrade 等）は追跡のみ amount=0。
3. gate `kind!=ok`（unknown/exhausted）では予約を作らず、enforce false の claim 継続と両立。
4. `capacity.reserved` の書き手は `write_status` のみ（1 パス 1 書き手）。claim は `reservations/` のみ触る。
5. consistency-sweep: coordination / batch / loop / board / prioritize / agent-flow ledger / schema / doctor 読取形。

**未解決 / 範囲外（直していない）**

- amigos 側の明示 env 引継ぎ（project/flow と同一 `AGENT_RESERVATION_ID` 規約。amigos 未配線）
- 残量の連続優先度化・中央スケジューラ（非目標）
- S1 `artifacts/architecture.md` 削除
- U5 dashboard JS 完全置換

{"ok": true, "artifacts": ["artifacts/t9/REPORT.md"]}

<!-- context-read-report {"used":["tools/agent-project/agent_project/coordination.py","tools/agent-tools/agentcore/agentcore/board.py","tools/agent-flow/agent_flow/agent.py","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/INPUT_CONTRACT.md","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/REPORT.md","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/contract.json"],"extra":["tools/agent-project/agent_project/batch.py","tools/agent-project/agent_project/loop.py","tools/agent-project/agent_project/board.py","tools/agent-project/agent_project/prioritize.py","tools/agent-project/agent_project/doctor.py","schemas/node-budget-summary.schema.json","docs/plans/2026-07-29-agent-tools-distributed-credit-knowledge-plan.md","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/t11/REPORT.md","tools/agent-project/tests/test_coordination.py","tools/agent-project/tests/test_node_budget_summary.py"]} -->
