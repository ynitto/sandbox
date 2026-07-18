---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: dashboard-163827
kind: plan-review
---

# 実行前レビュー: dashboard-163827 — dashboard で一貫性ゲートの状態把握と有効化を支援する

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : dashboard で一貫性ゲートの状態把握と有効化を支援する
- verify : `grep -nE 'regression_cmd|intake_cmd|一貫性ゲート' tools/agent-dashboard/src/renderer/renderer.js tools/agent-dashboard/src/features/agent-project/main/project.js && node tools/agent-dashboard/test/needs-diagnosis.test.js && node tools/agent-dashboard/test/overview-ui.test.js`
- why: パッケージ内マジック配線を外した後も、人が regression/intake の有無とゲート失敗の意味を画面から判断・対処できるようにするため。
- out_of_scope: agent-project 本体のフック実装・done 不変条件を破る UI からの状態書換
- hints: 概要またはプロジェクト情報に regression_cmd/intake_cmd（設定の有無）を可視化し、未結線時は README と同じ有効化導線（設定編集／sibling CLI）を示す。needs の codd-gate / 回帰失敗要約（needs-diagnosis）の可読性を落とさない。公式契約（needs/inbox/commands）以外へ書かない。実装後は agent-reviewer で UX レビュー。
- after: codd-gate-163827, sibling-163827
- workspace: agent-dashboard
- charter: v1
- assess: c=2 r=1 a=1
- source : charter

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve dashboard-163827`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject dashboard-163827 --reason ...`。 -->
