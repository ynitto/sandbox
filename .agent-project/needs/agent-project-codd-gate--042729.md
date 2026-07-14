---
status: proposed
date: 2026-07-15
decision-makers: [human]
task-id: agent-project-codd-gate--042729
kind: blocked
---

# 要対応: agent-project-codd-gate--042729 — agent-projectにcodd-gate自動検出とregression/intake結線を完成させ連携を有効化する

## Context and Problem Statement

- なぜ: [agent-error:env] 環境の問題（実行環境の問題）: 実行環境の問題です（モデル名・CLI の導入・PATH を確認してください） タスクの内容の問題ではないため、リトライ回数は消費していません。環境を直してから approve すると、同じ run の続き（失敗した工程だけ）から再開します。
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 32 ファイル
    - .agent-project/backlog/agent-project-codd-gate--042729.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/docs/agent-project-readme-caveat-addendum.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/docs/codd-gate-design-detection-addendum.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/gate/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/loop/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/synth/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/t1/contract.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/t2/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/artifacts/t4/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/events/orchestrator.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/events/worker-1.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r1/events/worker-2.jsonl
    - …他 20 件
- 検証: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml && grep -E '^[[:space:]]*intake_cmd:.*codd-gate tasks' .agent/agent-project.yaml && PYTHONPATH=tools/agent-project python3 -c 'from codd_gate_status import detect_status; s=detect_status(); assert s.usable and s.command("verify", "--base", "HEAD")' && python3 -m pytest tools/agent-project/tests/test_codd_gate_detect.py tools/agent-project/tests/test_codd_gate_routing.py -q` → FAIL（exit=2 失敗した工程: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` grep: .agent/agent-project.yaml: No such file or directory）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve agent-project-codd-gate--042729`。 -->
