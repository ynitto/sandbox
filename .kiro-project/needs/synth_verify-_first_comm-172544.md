---
status: proposed
date: 2026-07-13
decision-makers: [human]
task-id: synth_verify-_first_comm-172544
kind: review
risk: med
---

# 要対応: synth_verify-_first_comm-172544 — synth_verify の _first_command_line がコードフェンス内のコマンドを拾えず、LLM が前置きを付けると verify 合成が失敗する問題を修正する

## Context and Problem Statement

- なぜ: 検証は通っている（verify=PASS）。人の検収を待っている理由: このタスクが承認ゲートの対象（review / policy.gate）。内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す
- 状態: review（検収待ち・verify=PASS）

## 判断材料（成果物の所在・差分・検証）
- 成果物: ブランチ `kp/synth_verify-_first_comm-172544`（2 ファイル変更・base `main`）
- 所在: /Users/nitto/Workspace/sandbox
- 差分を見る: `git -C /Users/nitto/Workspace/sandbox diff main...origin/kp/synth_verify-_first_comm-172544`
- 変更ファイル（2 件）:
    - tools/kiro-project/kiro-project.py
    - tools/kiro-project/tests/test_kiro_project.py
- 実行先: local
- 検証: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` → PASS（15 passed, 564 deselected）

## リスク
- 総合: 中（protect/avoid=高、リトライ・大差分・合成 verify=中）
- リトライ: 6 回（NG 積み直しを経た成果）
- 変更ファイル: 2 件（tools/kiro-project/kiro-project.py, tools/kiro-project/tests/test_kiro_project.py）
- 投入時採点: c=2 r=2 a=1（c=複雑さ r=リスク a=曖昧さ・各1-3）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して done 確定するなら `kiro-project approve synth_verify-_first_comm-172544`。
     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->
