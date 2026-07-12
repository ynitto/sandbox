---
status: proposed
date: 2026-07-13
decision-makers: [human]
task-id: macOS-kiro-flow-git-4-gr-171537
kind: review
risk: med
---

# 要対応: macOS-kiro-flow-git-4-gr-171537 — macOS で失敗する kiro-flow の git 自己修復テスト 4 件を修正し、テストスイート全体を green にする

## Context and Problem Statement

- なぜ: 検証は通っている（verify=PASS）。人の検収を待っている理由: このタスクが承認ゲートの対象（review / policy.gate）。内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す
- 状態: review（検収待ち・verify=PASS）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `kp/macOS-kiro-flow-git-4-gr-171537`（1 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/kp/macOS-kiro-flow-git-4-gr-171537`
- 変更ファイル（1 件）:
    - tools/kiro-flow/tests/test_kiro_flow.py
- 実行先: local
- 検証: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q` → PASS（exit=0 ........................................ [ 72%] ........................................................................ [ 79%] .................................................................）

## リスク
- 総合: 中（protect/avoid=高、リトライ・大差分・合成 verify=中）
- リトライ: 6 回（NG 積み直しを経た成果）
- 変更ファイル: 1 件（tools/kiro-flow/tests/test_kiro_flow.py）
- 投入時採点: c=2 r=1 a=1（c=複雑さ r=リスク a=曖昧さ・各1-3）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `kiro-project approve macOS-kiro-flow-git-4-gr-171537`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
