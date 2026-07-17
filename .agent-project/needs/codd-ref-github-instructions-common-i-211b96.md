---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: codd-ref-github-instructions-common-i-211b96
kind: plan-review
---

# 実行前レビュー: codd-ref-github-instructions-common-i-211b96 — .github/instructions/common.instructions.md の壊れた参照 ~/.copilot/skill-registry.json を修正する（repo src）

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : .github/instructions/common.instructions.md の壊れた参照 ~/.copilot/skill-registry.json を修正する（repo src）
- verify : `codd-gate check --repo-dir src=. --refs .github/instructions/common.instructions.md`
- note: .github/instructions/common.instructions.md 行54 の ~/.copilot/skill-registry.json が実在しない（inline）
- assess: c=1 r=1 a=1
- priority: 1
- source : enqueue

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve codd-ref-github-instructions-common-i-211b96`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject codd-ref-github-instructions-common-i-211b96 --reason ...`。 -->
