---
applyTo: "tools/agent-project/**,tools/agent-flow/**,tools/agent-amigos/**,tools/agent-board/**,tools/agent-dashboard/**,tools/agent-loop/**,tools/agent-audit/**,tools/agent-tools/**,tools/codd-gate/**,schemas/**,docs/designs/agent-*.md,docs/designs/codd-gate-design.md"
---

# agent-tools ファミリーの作業ゲート

対象パス（agent-* ツール群・codd-gate・schemas・その設計書）への機能追加・設計変更では、
[`docs/designs/agent-tools-concept.md`](../../docs/designs/agent-tools-concept.md)（コンセプト正典）を
**着手前に読み**、その §8「このリポジトリでの強制」に従う。要点:

1. 変更が三本柱（チーム利用 / 人介在の最適化 / 資源効率）と原則 C1〜C10 のどれに効くかを一言で言えること。
   言えない機能は着手せず、ユーザーに差し戻す。
2. 効く柱・原則を成果物（設計書の「背景と課題」、または PR 説明）に 1 行で明記する。
3. 原則と衝突する変更は黙って迂回しない。設計を変えるか、正典の改訂を同じ PR で提案して
   人の承認を得るか、の 2 択。
4. レビュー（人・agent-reviewer とも）は正典 §8 のチェックリストを通す。チェックリストの
   正典はあちらであり、ここへ複製しない。

バグ修正・タイポ修正・既存方針の範囲内のリファクタリングは 1〜2 の明記を省いてよいが、
3〜4 は常に適用する。
