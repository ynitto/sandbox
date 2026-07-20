---
status: proposed
date: 2026-07-20
decision-makers: [human]
task-id: sibling-163827
kind: review
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/sibling-163827","ref":"origin/ap/sibling-163827","files":["tools/agent-project/GUIDE.md","tools/agent-project/README.md","tools/agent-project/codd_gate_base.py","tools/agent-project/codd_gate_regression.py","tools/agent-project/codd_gate_wiring.py","tools/agent-project/tests/test_codd_gate_base.py","tools/agent-project/tests/test_codd_gate_regression.py","tools/agent-project/tests/test_codd_gate_routing.py","tools/agent-project/tests/test_codd_gate_wiring.py"],"files_total":9,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/sibling-163827","mr_url":""}]
---

# 要対応: sibling-163827 — sibling 自動検出レイヤと利用手順を新境界へ追随させる

## Context and Problem Statement

- なぜ: 承認されたが成果ブランチを統合できない: main と ap/sibling-163827 の自動統合で競合しました。成果ブランチを更新して再検収してください: Auto-merging tools/agent-project/GUIDE.md
Auto-merging tools/agent-project/README.md
CONFLICT (content): Merge conflict in tools/agent-project/README.md
Automatic merge failed; fix conflicts and then commit the result.
- 状態: review（検収待ち・verify=PASS）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `ap/sibling-163827`（9 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/sibling-163827`
- 変更ファイル（9 件）:
    - tools/agent-project/GUIDE.md
    - tools/agent-project/README.md
    - tools/agent-project/codd_gate_base.py
    - tools/agent-project/codd_gate_regression.py
    - tools/agent-project/codd_gate_wiring.py
    - tools/agent-project/tests/test_codd_gate_base.py
    - tools/agent-project/tests/test_codd_gate_regression.py
    - tools/agent-project/tests/test_codd_gate_routing.py
    - tools/agent-project/tests/test_codd_gate_wiring.py
- 実行先: local

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `agent-project approve sibling-163827`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
