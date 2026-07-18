---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: codd-gate-163827
kind: plan-review
---

# 実行前レビュー: codd-gate-163827 — codd-gate 連携の目標境界を設計書に固定する

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : codd-gate 連携の目標境界を設計書に固定する
- verify : `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md`
- why: 『パッケージは汎用フックのみ・codd_gate_* は sibling 任意部品』を実装前に文書で合意しないと、整理の完了判定と dashboard の見せ方がぶれるため。
- out_of_scope: agent_project / dashboard の実装変更やテスト改修
- hints: ドキュメントは slop-police スキルで整える。正典は docs/designs/codd-gate-design.md §4（差し込み点 E1–E3）と §4.1（自動検出レイヤ）。受入の `! git grep ... _apply_codd_gate|_codd_gate|import codd_gate` を設計上の完了条件として明記し、永続化は `codd_gate_regression.py`・有効化は yaml/CLI のみ、と境界を書く。tools/agent-project/README.md の一貫性ゲート節も同じ境界に揃える。
- workspace: agent-project
- charter: v1
- assess: c=2 r=1 a=1
- source : charter

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve codd-gate-163827`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject codd-gate-163827 --reason ...`。 -->
