---
status: proposed
date: 2026-07-12
decision-makers: [human]
task-id: kiro-project-codd-gate-171537
kind: blocked
---

# 要対応: kiro-project-codd-gate-171537 — kiro-project に codd-gate 自動検出を実装し、差分ゲート・受入判定・負債取り込みへ結線する

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: exit=5 512 deselected in 0.19s
- 状態: blocked（kiro-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox/.kiro-project
- 実行先: local
- 差分: 56 ファイル
    - .kiro-project/bus/runs/run-20260712-192756-4329/claims/t10/worker-1.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/claims/t12/worker-2.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/claims/t9-m3/worker-2.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/claims/t9-m4/worker-1.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/final.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t10.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t12.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t9-m1.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t9-m2.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t9-m3.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/results/t9-m4.json
    - .kiro-project/bus/runs/run-20260712-200046-1141/claims/t1/worker-1.json
    - …他 44 件
- 検証: `python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → FAIL（exit=5 512 deselected in 0.19s）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `kiro-project approve kiro-project-codd-gate-171537`。 -->
