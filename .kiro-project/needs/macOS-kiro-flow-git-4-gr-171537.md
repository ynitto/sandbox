---
status: proposed
date: 2026-07-12
decision-makers: [human]
task-id: macOS-kiro-flow-git-4-gr-171537
kind: blocked
---

# 要対応: macOS-kiro-flow-git-4-gr-171537 — macOS で失敗する kiro-flow の git 自己修復テスト 4 件を修正し、テストスイート全体を green にする

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: exit=1 AILED tools/kiro-flow/tests/test_kiro_flow.py::GitDistributedTests::test_sync_push_self_heals_on_object_corruption
FAILED tools/kiro-flow/tests/test_kiro_flow.py::StateGitSyncTests::test_empty_objects_state_clone_is_rebuilt_on_reuse
FAILED tools/kiro-flow/tests/test_kiro_flow.py::StateGitSyncTests::test_state_sync_self_heals_on_object_corruption_midflight
4 failed, 896 passed in 118.22s (0:01:58)
- 状態: blocked（kiro-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 24f717c9 remove: delete kiro-flow configuration file
- 所在: /Users/nitto/Workspace/sandbox/.kiro-project
- 実行先: local
- 差分: 68 ファイル
    - .github/skills/flow-planner/scripts/plan.py
    - .gitignore
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t27/report.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/claims/t28/worker-1.json
    - .kiro-project/bus/runs/run-20260712-185720-3472/results/t27.json
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t1/pytest-exit-code.txt
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t1/report.md
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t2/git-self-heal-inventory.md
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t3/git-self-repair-test-assumptions.md
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t4/macos-path-audit.md
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t5/git-config-audit.md
    - .kiro-project/bus/runs/run-20260712-192756-4329/artifacts/t6/bsd-gnu-command-audit.md
    - …他 56 件
- 検証: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` → FAIL（exit=1 AILED tools/kiro-flow/tests/test_kiro_flow.py::GitDistributedTests::test_sync_push_self_heals_on_object_corruption FAILED tools/kiro-flow/tests/test_kiro_flow.py::StateGitSyncTests::test_empty_）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `kiro-project approve macOS-kiro-flow-git-4-gr-171537`。 -->
