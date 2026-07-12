---
status: proposed
date: 2026-07-12
decision-makers: [human]
task-id: synth_verify-_first_comm-172544
kind: blocked
---

# 要対応: synth_verify-_first_comm-172544 — synth_verify の _first_command_line がコードフェンス内のコマンドを拾えず、LLM が前置きを付けると verify 合成が失敗する問題を修正する

## Context and Problem Statement

- なぜ: 繰り返し NG（retries=4）: exit=5 512 deselected in 0.20s
- 状態: blocked（kiro-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
- 成果物: git: 未コミットの変更あり
- 所在: /Users/nitto/Workspace/sandbox/.kiro-project
- 実行先: local
- 差分: 99 ファイル
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t1/first_command_line-current.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t10/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t11/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t12/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t13/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t14/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t15/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t16/first-command-line-fenced-preamble-test.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t17/prompt-prefix-test-report.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t18/result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t19/multiple-fences-test-result.md
    - .kiro-project/bus/runs/run-20260712-185720-3472/artifacts/t2/synth_verify-call-path.md
    - …他 87 件
- 検証: `python3 -m pytest tools/kiro-project/tests -q -k first_command_line` → FAIL（exit=5 512 deselected in 0.20s）

## Decision Outcome

<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->
- [x] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。
     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。
     コマンドなら `kiro-project approve synth_verify-_first_comm-172544`。 -->
