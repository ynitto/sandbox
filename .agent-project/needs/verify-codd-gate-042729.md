---
status: proposed
date: 2026-07-15
decision-makers: [human]
task-id: verify-codd-gate-042729
kind: plan-review
---

# 実行前レビュー: verify-codd-gate-042729 — verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる
- verify : `PYTHONPATH=tools/agent-project python3 -c 'from agent_project import _first_command_line; assert _first_command_line("検証コマンド:\ncodd-gate verify --base \"$KIRO_BASE_REV\"") == "codd-gate verify --base \"$KIRO_BASE_REV\""'`
- workspace: sandbox
- charter: v1
- assess: c=1 r=1 a=1
- source : charter

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve verify-codd-gate-042729`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject verify-codd-gate-042729 --reason ...`。 -->
