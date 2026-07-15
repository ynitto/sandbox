---
status: proposed
date: 2026-07-15
decision-makers: [human]
task-id: docs-designs-README-042729
kind: blocked
---

# 要対応: docs-designs-README-042729 — 設計書の読み取り口（docs/designs/README）を作り主要設計への導線を通す

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base 567cbce7198f63117d75d459d3ee5d320b9123cf --repos .agent-project/repos.json` [codd-gate] エラー: repos レジストリが見つかりません: .agent-project/repos.json
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 63 ファイル
    - .agent-project/backlog/docs-designs-README-042729.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/gate/inventory.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/loop/link-check.json
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/loop/verify-command-log.txt
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/synth/README.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/t1/inventory.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/t2/inventory.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/t3/inventory.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/artifacts/t4/inventory.md
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/events/orchestrator.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/events/worker-1.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-docs-designs-README-042729-r0/events/worker-2.jsonl
    - …他 51 件
- 検証: `test -f docs/designs/README.md && grep -q 'agent-project-design.md' docs/designs/README.md && grep -q 'agent-flow-design.md' docs/designs/README.md && grep -q 'codd-gate-design.md' docs/designs/README.md && grep -q 'agent-tools-rename-design.md' docs/designs/README.md` → PASS（exit=0）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve docs-designs-README-042729`。 -->
