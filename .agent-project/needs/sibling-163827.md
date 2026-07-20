---
status: proposed
date: 2026-07-20
decision-makers: [human]
task-id: sibling-163827
kind: review
risk: med
delivery: [{"name":"sandbox","role":"write","url":"https://github.com/ynitto/sandbox","path":"/Users/nitto/Workspace/sandbox","base":"main","target":"main","branch":"ap/sibling-163827","ref":"origin/ap/sibling-163827","files":["tools/agent-project/GUIDE.md","tools/agent-project/README.md","tools/agent-project/codd_gate_base.py","tools/agent-project/codd_gate_regression.py","tools/agent-project/codd_gate_wiring.py","tools/agent-project/tests/test_codd_gate_base.py","tools/agent-project/tests/test_codd_gate_regression.py","tools/agent-project/tests/test_codd_gate_routing.py","tools/agent-project/tests/test_codd_gate_wiring.py"],"files_total":9,"diff_cmd":"git -C /Users/nitto/Workspace/sandbox diff main...origin/ap/sibling-163827","mr_url":""}]
---

# 要対応: sibling-163827 — sibling 自動検出レイヤと利用手順を新境界へ追随させる

## Context and Problem Statement

- なぜ: 検証は通っている（verify=PASS）。人の検収を待っている理由: このタスクが承認ゲートの対象（review / policy.gate）。内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す
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
- 到達工程: verify（検証）
- 検証: `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py' && grep -nE 'codd_gate_regression|regression_cmd|intake_cmd' tools/agent-project/README.md && ! grep -nE 'build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring' tools/agent-project/README.md` → PASS（exit=0 codd-gate を検出できたのに `regression_cmd`/`intake_cmd` が 447:- **取り込みコマンド（intake_cmd）**: 外部の決定的ゲート/検出器を **watch の周期で pull** する汎用フック（push 型の 448:  inbox と対）。設定 `intake_cmd:`（CLI `--intake-cmd`）のコマンドをパ）

## リスク
- 総合: 中（protect/avoid=高、リトライ・大差分・合成 verify=中）
- リトライ: 4 回（NG 積み直しを経た成果）
- 変更ファイル: 9 件（tools/agent-project/GUIDE.md, tools/agent-project/README.md, tools/agent-project/codd_gate_base.py, tools/agent-project/codd_gate_regression.py, tools/agent-project/codd_gate_wiring.py 他 4 件）
- 投入時採点: c=2 r=2 a=2（c=複雑さ r=リスク a=曖昧さ・各1-3）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `agent-project approve sibling-163827`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
