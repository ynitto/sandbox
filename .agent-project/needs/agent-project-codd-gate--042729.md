---
status: proposed
date: 2026-07-17
decision-makers: [human]
task-id: agent-project-codd-gate--042729
kind: blocked
delivery: [{"name":"sandbox","role":"reference","url":"https://github.com/ynitto/sandbox","path":"","base":"main","branch":"","ref":"","files":[],"files_total":0,"diff_cmd":"","mr_url":""}]
---

# 要対応: agent-project-codd-gate--042729 — agent-projectにcodd-gate自動検出とregression/intake結線を完成させ連携を有効化する

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: workspace repo の clone 失敗（https://github.com/ynitto/sandbox@ap/agent-project-codd-gate--042729）: Cloning into '/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-verify-5kvvec5e/repo'...
fatal: Remote branch ap/agent-project-codd-gate--042729 not found in upstream origin
- 状態: blocked（agent-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox-agent-state/.agent-project
- 実行先: local
- 差分: 26 ファイル
    - .agent-project/agent-project.yaml
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t1/investigation-and-policy.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t12-fix-and-verify-canonical-codd-gate/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t2/detection-fallback-verification.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t4/intake-pipeline-live-verification.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t8/report.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/artifacts/t9/adversarial-verification.md
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/events/orchestrator.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/events/worker-1.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/events/worker-2.jsonl
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/final.json
    - .agent-project/bus/runs/req-ef1f92c3-agent-project-codd-gate--042729-r3-v2/graph.json
    - …他 14 件
- 検証: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` → FAIL（workspace repo の clone 失敗（https://github.com/ynitto/sandbox@ap/agent-project-codd-gate--042729）: Cloning into '/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-verify-5kvvec5e/repo'... fatal: Re）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `agent-project approve agent-project-codd-gate--042729`。 -->
