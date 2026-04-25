---
id: mem-20260411-001
title: "execution method skills analysis"
created: "2026-04-11"
updated: "2026-04-11"
status: active
scope: "shared"
memory_type: procedural
importance: high
retention_score: "1.0"
tags: [copilot-scope:OTcxZDljMDAtZjQ1YS00NWFkLWI2ZGItMjYwNGFmMzk5ZTk4, copilot-memory, imported]
related: []
access_count: 4
last_accessed: "2026-04-19"
user_rating: 0
correction_count: 0
share_score: 90
promoted_from: "mem-20260411-001"
consolidated_from: []
consolidated_to: ""
summary: "# 実行手法スキル分析（2026-04-04）"
---

# execution method skills analysis

## コンテキスト
VSCode Copilot Memory からインポート（ソース: /Users/nitto/Library/Application Support/Code/User/workspaceStorage/40ca8287109c8a05fb703aa86bc590cf/github.copilot-chat/memory-tool/memories/OTcxZDljMDAtZjQ1YS00NWFkLWI2ZGItMjYwNGFmMzk5ZTk4/execution-method-skills-analysis.md）

## 詳細
# 実行手法スキル分析（2026-04-04）

## 定義
**実行手法スキル** = ドメイン知識ではなく「進め方」「手順」「プロセス」を提供するスキル

例：
- TDD: Red-Green-Refactor サイクル | 特定言語・技術ではない
- scrum-master: 7フェーズのフロー | プロジェクト管理手順
- brainstorming: 段階的対話フロー | アイデア→設計への進め方

---

## 既存の実行手法スキル（確認済み）

| # | スキル名 | 1行要約 | キーフェーズ/ステップ |
|---|---------|--------|-------------------|
| 1 | `tdd-executing` | Red-Green-Refactor サイクルの実行 | RED→GREEN→REFACTOR (反復) |
| 2 | `scrum-master` | 7フェーズのスプリント管理 | Phase 1-7 |
| 3 | `skill-mentor` | 軽量オーケストレーター（1ゴール） | 振り分け→委譲→検証 |
| 4 | `brainstorming` | アイデア→設計への段階的対話 | 質問→設計提示→承認 |
| 5 | `self-checking` | 成果物の反復評価改善 | 生成→評価→改善→再評価 |
| 6 | `systematic-debugging` | 仮説→計装→検証の体系的フロー | Phase 1-4 (根本原因特定→修正) |
| 7 | `doc-coauthoring` | 3ステージ共同執筆ワークフロー | Stage 1-3 (コンテキスト→構造→読者テスト) |
| 8 | `requirements-definer` | 対話的要件抽出フロー | Step 1-2+ (対話→要件JSON出力) |
| 9 | `domain-modeler` | DDDモデル設計/逆引きの2モード | Mode 判定→フロー実行 |
| 10 | `skill-creator` | 4モード選定と各マクロプロセス | Mode A-D の選定と実行 |
| 11 | `skill-selector` | タスク分析→スキル選定 | Step 1-2 |
| 12 | `sprint-reviewer` | スプリント完了判定・レトロ | Step 1-2+ |
| 13 | `patent-coach` | 発明深掘りのソクラテス式対話 | Phase 1-4 |
| 14 | `patent-writer` | 特許明細書作成のヒアリング構造 | ラウンド1-2 (構造化質問) |
| 15 | `deep-research` | 5ステップ多角調査フロー | Step 1-5 |
| 16 | `performance-profiler` | パフォーマンスボトルネック特定フロー | Step 0-3+ (スコープ→静的→動的) |
| 17 | `api-designer` | REST/GraphQL設計フロー | Step 1-6+ |
| 18 | `agent-reviewer` | Perspective選択→並列実行→集約 | - |
| 19 | `ci-cd-configurator` | CI/CDパイプライン構築フロー | Step 1-N |
| 20 | `code-simplifier` | リファクタリング分析→修正フロー | ワークフロー |
| 21 | `gitlab-idd` | 非同期分散タスク駆動開発 | ロール選択→実行 |
| 22 | `skill-evaluator` | スキル評価フロー（静的→動的） | Step 0-1 |
| 23 | `technical-writer` | 5原則ベースのドキュメント作成 | 原則適用型フロー |

---

## 実行手法スキルの分類

### A. オーケストレーション（複数タスク統合管理）
- scrum-master, skill-mentor, gitlab-idd, agent-reviewer

### B. 段階的対話フロー（ユーザー対話を含む）
- brainstorming, requirements-definer, patent-coach, patent-writer, doc-coauthoring

### C. 反復改善ループ
- tdd-executing, self-checking, code-simplifier

### D. 体系的調査・分析
- deep-research, systematic-debugging, performance-profiler, skill-evaluator

### E. 設計・構築フロー
- domain-modeler, api-designer, ci-cd-configurator, technical-writer, skill-creator, skill-selector

### F. 評価・レビュー
- sprint-reviewer

## 学び・結論
（インポート時は未評価。内容を確認後 rate_memory.py で評価してください）
