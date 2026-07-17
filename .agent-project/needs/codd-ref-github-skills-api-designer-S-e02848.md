---
status: proposed
date: 2026-07-18
decision-makers: [human]
task-id: codd-ref-github-skills-api-designer-S-e02848
kind: plan-review
---

# 実行前レビュー: codd-ref-github-skills-api-designer-S-e02848 — .github/skills/api-designer/SKILL.md の壊れた参照 references/rest-design-guide.md を修正する（repo src）

## Context and Problem Statement

- なぜ: 新規タスクの実行前レビュー（承認されるまで実行しません）
- 状態: proposed（実行前レビュー待ち・未実行）

## タスク定義（レビュー対象）
- title  : .github/skills/api-designer/SKILL.md の壊れた参照 references/rest-design-guide.md を修正する（repo src）
- verify : `codd-gate check --repo-dir src=. --refs .github/skills/api-designer/SKILL.md`
- note: .github/skills/api-designer/SKILL.md 行91 の references/rest-design-guide.md が実在しない（link）
- assess: c=1 r=1 a=1
- priority: 1
- source : enqueue

## Decision Outcome

<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->
- [ ] 確定（このボックスを [x] にして保存すると取り込みます）

<!-- 承認して実行を許可するなら `agent-project approve codd-ref-github-skills-api-designer-S-e02848`（または空のまま [x]）。
     差し戻す（agent-project にタスクを修正させる）なら下に修正指示を書いて [x]。
     却下（廃止して関連バックログを再計画）なら `agent-project reject codd-ref-github-skills-api-designer-S-e02848 --reason ...`。 -->
