---
status: proposed
date: 2026-07-16
decision-makers: [human]
task-id: verify-codd-gate-042729
kind: blocked
---

# 要対応: verify-codd-gate-042729 — verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: exit=1 失敗した工程: `python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'`（それより前の工程は成功） Traceback (most recent call last):
  File "<string>", line 1, in <module>
    from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'agent_project'
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 21 ファイル
    - .agent-project/backlog/verify-codd-gate-042729.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t5/report.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t6/completion_command.exit
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t6/completion_command.out
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t6/pytest_test_agent_project.exit
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t6/pytest_test_agent_project.out
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t6/report.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/t7/report.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/verify-codd-gate-final-check-04/report.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/artifacts/verify-codd-gate-scope-and-pass-01/report.md
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/events/worker-1.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-verify-codd-gate-042729-r1/events/worker-2.jsonl
    - …他 9 件
- 検証: `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'` → FAIL（exit=1 失敗した工程: `python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_RE）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve verify-codd-gate-042729`。 -->
