---
status: proposed
date: 2026-07-19
decision-makers: [human]
task-id: agent_project-codd_gate-163827
kind: blocked
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/agent_project-codd_gate-163827","ref":"origin/ap/agent_project-codd_gate-163827","files":["tools/agent-project/README.md","tools/agent-project/agent_project/configfile.py","tools/agent-project/agent_project/doctor.py","tools/agent-project/agent_project/model.py","tools/agent-project/codd_gate_debt.py","tools/agent-project/codd_gate_wiring.py","tools/agent-project/tests/test_agent_project.py"],"files_total":7,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/agent_project-codd_gate-163827","mr_url":""}]
---

# 要対応: agent_project-codd_gate-163827 — agent_project を codd_gate 非依存の汎用フックへ整理する

## Context and Problem Statement

- なぜ: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base e51dd5bb927c12fc3c79cccdcd2d46e4bf794b14 --repos ./repos.json` [codd-gate] エラー: スキャン可能な repo がありません（--repo-dir <name>=<dir> か --sync を指定）
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/agent_project-codd_gate-163827`（7 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/agent_project-codd_gate-163827`
- 変更ファイル（7 件）:
    - tools/agent-project/README.md
    - tools/agent-project/agent_project/configfile.py
    - tools/agent-project/agent_project/doctor.py
    - tools/agent-project/agent_project/model.py
    - tools/agent-project/codd_gate_debt.py
    - tools/agent-project/codd_gate_wiring.py
    - tools/agent-project/tests/test_agent_project.py
- 実行先: local
- 検証: `PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestIntake.test_run_intake_enqueues_and_dedups_by_id TestLoopEngineering.test_regression_gate_blocks_on_failure TestLoopEngineering.test_regression_gate_passes && ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' -- tools/agent-project/agent_project` → PASS（exit=0 --- 通知（要対応）--- # 要対応（agent-project）  ## 判断待ち（blocked） - T1: x     なぜ: 回帰検知: グローバル検査 `false` 失敗 — exit=1 失敗した工程: `false`      対応: needs/T1.md に方針を書く、または `approve T1` / `hold T1`  ... -----------）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve agent_project-codd_gate-163827`。 -->
