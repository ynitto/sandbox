---
status: proposed
date: 2026-07-16
decision-makers: [human]
task-id: docs-designs-README-042729
kind: blocked
---

# 要対応: docs-designs-README-042729 — 設計書の読み取り口（docs/designs/README）を作り主要設計への導線を通す

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base 45a480f10edd965081cc9a4b3afcfbb7a916c2e9 --repos .agent-project/repos.json` [codd-gate] エラー: repos レジストリが見つかりません: .agent-project/repos.json
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: (変更なし)
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: baseline 以降の変更なし
- 検証: `test -f docs/designs/README.md && grep -q 'agent-project-design.md' docs/designs/README.md && grep -q 'agent-flow-design.md' docs/designs/README.md && grep -q 'codd-gate-design.md' docs/designs/README.md && grep -q 'agent-tools-rename-design.md' docs/designs/README.md` → PASS（exit=0）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve docs-designs-README-042729`。 -->
