---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: agent_project-codd_gate-163827
kind: plan-review
---

# 実行前レビュー: agent_project-codd_gate-163827 — agent_project を codd_gate 非依存の汎用フックへ整理する

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : agent_project を codd_gate 非依存の汎用フックへ整理する
- verify : `PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestIntake.test_run_intake_enqueues_and_dedups_by_id TestLoopEngineering.test_regression_gate_blocks_on_failure TestLoopEngineering.test_regression_gate_passes && ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' -- tools/agent-project/agent_project`
- why: 設計の『本体は無改造・差し込み点のみ』をコードで真にし、受入の grep 条件と intake/regression 回帰テストを同時に満たすため。
- out_of_scope: dashboard UI・設計書の文章だけの推敲・codd-gate 本体（tools/codd-gate）の仕様変更
- hints: 除去/改名対象: configfile._apply_codd_gate_auto_wiring、doctor._codd_gate_wiring_module / doctor_codd_gate_findings、model._codd_gate_debt_module と `import codd_gate_*`。intake は schemas/task 相当の汎用 JSON パース＋ id 冪等のまま維持。自動配線は sibling（codd_gate_wiring / codd_gate_regression）か設定明示に寄せ、パッケージ内に codd_gate 名を残さない。TestCoddGateAutoWiring など `_codd_gate_wiring_module` を mock するテストも新境界へ追随。完了後は agent-reviewer で境界レビュー。
- after: codd-gate-163827
- workspace: agent-project
- charter: v1
- assess: c=2 r=2 a=1
- source : charter

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve agent_project-codd_gate-163827`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject agent_project-codd_gate-163827 --reason ...`。 -->
