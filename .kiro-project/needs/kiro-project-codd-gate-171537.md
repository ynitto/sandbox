---
status: proposed
date: 2026-07-13
decision-makers: [human]
task-id: kiro-project-codd-gate-171537
kind: review
risk: med
---

# 要対応: kiro-project-codd-gate-171537 — kiro-project に codd-gate 自動検出を実装し、差分ゲート・受入判定・負債取り込みへ結線する

## Context and Problem Statement

- なぜ: 検証は通っている（verify=PASS）。人の検収を待っている理由: このタスクが承認ゲートの対象（review / policy.gate）。内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す
- 状態: review（検収待ち・verify=PASS）

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
- 検証: `python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` → PASS（exit=0 .............................                                            [100%] 29 passed, 594 deselected in 0.17s 差分: sandbox 16b0b7c09bb41cf78b1997517157115e652099b0..作業ツリー（0 ファイル） OK: 一貫性ゲート）

## リスク
- 総合: 中（protect/avoid=高、リトライ・大差分・合成 verify=中）
- リトライ: 9 回（NG 積み直しを経た成果）
- 変更ファイル: 5 件（.kiro-project/repos.json, tools/kiro-project/codd_gate_detect.py, tools/kiro-project/codd_gate_invoke.py, tools/kiro-project/tests/test_codd_gate_detect.py, tools/kiro-project/tests/test_codd_gate_invoke.py）
- 投入時採点: c=2 r=2 a=2（c=複雑さ r=リスク a=曖昧さ・各1-3）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `kiro-project approve kiro-project-codd-gate-171537`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
