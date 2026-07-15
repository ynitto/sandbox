---
status: proposed
date: 2026-07-16
decision-makers: [human]
task-id: agent-project-codd-gate--042729
kind: blocked
---

# 要対応: agent-project-codd-gate--042729 — agent-projectにcodd-gate自動検出とregression/intake結線を完成させ連携を有効化する

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base 4111d87a4729a5d0d6291f11a4f1de6b790bec5f --repos .agent-project/repos.json` [codd-gate] エラー: repos レジストリが見つかりません: .agent-project/repos.json
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 73 ファイル
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t1/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t10-codd-gate-wiring-repair/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t11-codd-gate-end-to-end-verify/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t12-codd-gate-final-gate-verify/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t2/codd-gate-regression.patch
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t2/codd_gate_regression.py
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t2/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t2/test_codd_gate_regression.py
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t3/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t4/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t5/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1-v1/artifacts/t6/report.md
    - …他 61 件
- 検証: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` → PASS（exit=0 regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'）

## Decision Outcome

codd-gate verify --base 4111d87a4729a5d0d6291f11a4f1de6b790bec5f --repos repos.json　に修正


<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve agent-project-codd-gate--042729`。 -->
