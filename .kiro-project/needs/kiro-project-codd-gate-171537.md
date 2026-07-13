---
status: proposed
date: 2026-07-13
decision-makers: [human]
task-id: kiro-project-codd-gate-171537
kind: blocked
---

# 要対応: kiro-project-codd-gate-171537 — kiro-project に codd-gate 自動検出を実装し、差分ゲート・受入判定・負債取り込みへ結線する

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=9）: exit=5 589 deselected in 0.16s
- 状態: blocked（kiro-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `kp/kiro-project-codd-gate-171537`（5 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/kp/kiro-project-codd-gate-171537`
- 変更ファイル（5 件）:
    - .kiro-project/repos.json
    - tools/kiro-project/codd_gate_detect.py
    - tools/kiro-project/codd_gate_invoke.py
    - tools/kiro-project/tests/test_codd_gate_detect.py
    - tools/kiro-project/tests/test_codd_gate_invoke.py
- 実行先: local
- 検証: `python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → FAIL（exit=5 589 deselected in 0.16s）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `kiro-project approve kiro-project-codd-gate-171537`。 -->
