---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: codd-test-github-skills-code-to-specs-b5261c
kind: plan-review
---

# 実行前レビュー: codd-test-github-skills-code-to-specs-b5261c — .github/skills/code-to-specs/scripts/coverage_check.py のテストを追加する（repo src）

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : .github/skills/code-to-specs/scripts/coverage_check.py のテストを追加する（repo src）
- verify : `codd-gate check --repo-dir src=. --covered .github/skills/code-to-specs/scripts/coverage_check.py --need test`
- note: 接続マップ上でどのテストからも参照されていない
- assess: c=1 r=1 a=1
- source : enqueue

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve codd-test-github-skills-code-to-specs-b5261c`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject codd-test-github-skills-code-to-specs-b5261c --reason ...`。 -->
