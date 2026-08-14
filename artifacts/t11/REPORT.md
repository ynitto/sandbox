# t11 — Phase5 dashboard fleet/knowledge + doctor 検知

## (a) 成果

Phase5（P5）の観測 UI と doctor 検知を additive に実装。dashboard を第二 writer / 新正本にせず、人操作は `commands/` 契約投函のみ。

| 変更 | 内容 |
|------|------|
| `doctor.py` | `doctor_credit_knowledge_findings`（stale 射影・孤立 reservation・根拠なし active・未知 rule hash）。status 一次 + board 任意。`cmd_doctor` 常時接続 |
| `decisions.py` | `list_rule_adjudication` / `apply_rule_command`（promote/suspend/revise/deprecate） |
| `commands.py` | `rule-*`（および `rule_id` 付き promote/suspend/deprecate/revise）を ingest |
| `project.js` | `readNodeStatuses` に budget 射影、`readKnowledgeRules`、U5 reason_codes 定数 |
| `board-adapter.js` | 板ノードに budget 重ね + `eligibilityNote` |
| `overview.js` | fleet 表示（capacity/鮮度/予約/reason、board 重ね） |
| `orchestration.js` / `backlog.js` | 板の適格性・利用枠説明 |
| `history.js` + `actions.js` | 知識裁定一画面 + 契約投函ボタン |

**完了条件との対応**

| 条件 | 結果 |
|------|------|
| fleet: capacity / 鮮度 / 予約 / reason（status 一次・board 重ね） | 達した |
| board 入札・落札に適格性説明 | 達した（eligibilityNote） |
| knowledge: provenance/適用/PASS·fail·競合/遷移を一画面 | 達した（成果タブ） |
| 人操作は promote/suspend/revise/deprecate 投函のみ | 達した（commands/） |
| doctor: stale / 孤立予約 / 根拠なし active / 未知 hash | 達した |
| board なしでも分担把握可 | 達した（status 一次） |
| 裁定が一画面で完結 | 達した（history 知識節） |

## (b) 検証

| ID | コマンド | 結果 |
|----|----------|------|
| V1 | `unittest TestDoctorCreditKnowledge` | **3 ok** |
| V2 | `unittest TestLearnScopeAndExpiry`（rule command + Phase4 回帰） | **ok** |
| V3 | `unittest test_contract_baseline` 含む 27 tests | **OK** |
| V4 | `node test/budget-summary-parity.test.js` | **3 passed**（U5 reason_codes / contract_version 一致） |
| V5 | `node test/delegation-board.test.js` | **20 passed** |

## (c) 前提・未解決・範囲外

**採用した前提**

1. tip は t10（`9724692ab`）+ synth1。t9（reservation writer）は本 run 並行・未マージのため、`reservations/*.json` と `capacity.reserved>0` の読取検知を契約形で先に置く（writer 無しでも空＝所見なし）。
2. dashboard は読取 + commands 投函のみ。status `budget` の `can_accept` は再計算しない。
3. 未知 rule hash = Phase3 形式外（`sha256:<64hex>` 以外）。現行 rules.md 一致強制はしない（t10 前提）。
4. consistency-sweep: doctor / commands / decisions / fleet UI / board overlay / knowledge UI を一式更新。

**未解決 / 範囲外（直していない）**

- t9 reservation writer 本体（本タスク外。検知側のみ）
- `budget.js` ledger 集約の agentcore 完全置換（U5 は summary 語彙突き合わせまで）
- S1 `artifacts/architecture.md` 削除
- 資格情報預かり UI、新正本ストア

{"ok": true, "artifacts": ["artifacts/t11/REPORT.md"]}

<!-- context-read-report {"used":["tools/agent-dashboard/src/features/orchestration/main/budget.js","tools/agent-dashboard/src/features/agent-project/main/project.js","tools/agent-project/agent_project/doctor.py","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/INPUT_CONTRACT.md","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/REPORT.md","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/synth1/contract.json"],"extra":["tools/agent-project/agent_project/decisions.py","tools/agent-project/agent_project/commands.py","tools/agent-project/agent_project/rules.py","tools/agent-dashboard/src/features/delegation/main/board-adapter.js","tools/agent-dashboard/src/features/agent-project/main/actions.js","tools/agent-dashboard/src/renderer/sections/overview.js","tools/agent-dashboard/src/renderer/sections/history.js","tools/agent-dashboard/src/renderer/sections/orchestration.js","tools/agent-dashboard/src/renderer/sections/backlog.js","docs/plans/2026-07-29-agent-tools-distributed-credit-knowledge-plan.md","schemas/node-budget-summary.schema.json","/Users/nitto/.agents/flow/bus/runs/adhoc-20260814-092654-4851/artifacts/t10/REPORT.md","tools/agent-tools/agentcore/agentcore/board.py"]} -->
