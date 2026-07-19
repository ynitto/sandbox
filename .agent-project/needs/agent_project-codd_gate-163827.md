---
status: proposed
date: 2026-07-19
decision-makers: [human]
task-id: agent_project-codd_gate-163827
kind: review
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/agent_project-codd_gate-163827","ref":"origin/ap/agent_project-codd_gate-163827","files":["tools/agent-project/README.md","tools/agent-project/agent-project.yaml.example","tools/agent-project/agent_project/__init__.py","tools/agent-project/agent_project/config.py","tools/agent-project/agent_project/configfile.py","tools/agent-project/agent_project/doctor.py","tools/agent-project/agent_project/hooks.py","tools/agent-project/agent_project/model.py","tools/agent-project/codd_gate_debt.py","tools/agent-project/codd_gate_wiring.py","tools/agent-project/tests/test_agent_project.py"],"files_total":11,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/agent_project-codd_gate-163827","mr_url":""}]
---

# 要対応: agent_project-codd_gate-163827 — agent_project を codd_gate 非依存の汎用フックへ整理する

## Context and Problem Statement

- なぜ: 承認されたが成果ブランチを統合できない: main が作業開始後に更新されています。ap/agent_project-codd_gate-163827 を更新して再検収してください
- 状態: review（検収待ち・verify=PASS）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/agent_project-codd_gate-163827`（11 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/agent_project-codd_gate-163827`
- 変更ファイル（11 件）:
    - tools/agent-project/README.md
    - tools/agent-project/agent-project.yaml.example
    - tools/agent-project/agent_project/__init__.py
    - tools/agent-project/agent_project/config.py
    - tools/agent-project/agent_project/configfile.py
    - tools/agent-project/agent_project/doctor.py
    - tools/agent-project/agent_project/hooks.py
    - tools/agent-project/agent_project/model.py
    - tools/agent-project/codd_gate_debt.py
    - tools/agent-project/codd_gate_wiring.py
    - tools/agent-project/tests/test_agent_project.py
- 実行先: local

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `agent-project approve agent_project-codd_gate-163827`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
