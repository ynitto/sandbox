---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: sibling-163827
kind: plan-review
---

# 実行前レビュー: sibling-163827 — sibling 自動検出レイヤと利用手順を新境界へ追随させる

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : sibling 自動検出レイヤと利用手順を新境界へ追随させる
- verify : `PYTHONPATH=tools/agent-project python3 -m unittest discover -s tools/agent-project/tests -p 'test_codd_gate_*.py' && grep -nE 'codd_gate_regression|regression_cmd|intake_cmd' tools/agent-project/README.md && ! grep -nE 'build_config.*メモリ上で自動|_apply_codd_gate_auto_wiring' tools/agent-project/README.md`
- why: パッケージ外の検出・yaml 注入・doctor 所見の置き場と README の有効化手順を一貫させ、利用者が結線方法を迷わないようにするため。
- out_of_scope: agent_project パッケージ内への再結合・dashboard 変更
- hints: tools/agent-project/codd_gate_{detect,status,routing,base,debt,wiring,regression}.py は残し、責務を『検出・推奨文字列・yaml 冪等注入・（必要なら）CLI 所見』に限定。README / GUIDE から build_config メモリ自動配線の記述を消し、明示設定または `python3 codd_gate_regression.py --config …` へ誘導。docs/designs/codd-gate-design.md §4.1 の『現在地』も実装に合わせて更新（文章は slop-police）。
- after: agent_project-codd_gate-163827
- workspace: agent-project
- charter: v1
- assess: c=2 r=2 a=2
- source : charter

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve sibling-163827`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject sibling-163827 --reason ...`。 -->
