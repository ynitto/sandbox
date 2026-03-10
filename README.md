# Agent Skills

AIエージェント（GitHub Copilot / Claude Code）の能力を拡張するスキル集。
スキルは `.github/skills/` に配置された SKILL.md で定義され、エージェントが読み込むことで特定のタスクを高品質に実行できるようになる。

## スキル一覧（全 36 スキル）

### 基盤スキル（常時ロード）— 10

| スキル | 概要 |
|--------|------|
| **scrum-master** | プロンプトをバックログに分解し、スプリント単位でサブエージェントに委譲して実行するオーケストレーター |
| **git-skill-manager** | スキルの取得（pull）・共有（push）・昇格（promote）・バージョン管理・フィードバック記録を行う |
| **skill-creator** | 新しいスキルの作成・既存スキルの改良を支援する |
| **requirements-definer** | 曖昧な依頼を要件化し、バックログ化しやすい形に整理する |
| **skill-recruiter** | 外部URLのスキルを安全に取り込み、git-skill-manager に接続する |
| **skill-evaluator** | ワークスペース/インストール済みスキルを評価し、昇格・改良方針を提案する |
| **generating-skills-from-copilot-logs** | 履歴から再利用可能なスキル候補を発見・生成する |
| **sprint-reviewer** | スプリント完了時にタスク結果を評価し、次スプリントへの改善点をまとめる |
| **codebase-to-skill** | 既存コードベースを解析し、そのプロジェクト専用の SKILL.md を生成する |
| **ltm-use** | エージェントにセッションをまたいだ長期記憶（保存・検索・昇格・評価）を付与する |

### React 開発 — 3

| スキル | 概要 |
|--------|------|
| **react-frontend-coder** | React + TypeScript のフロントエンド実装を3スプリントで進める |
| **react-frontend-unit-tester** | Vitest + Testing Library による React コンポーネントのテストを作成する |
| **react-best-practices** | パフォーマンス最適化ルール集（非同期・バンドル・再レンダー等）を提供する |

### レビュー — 7

| スキル | 概要 |
|--------|------|
| **architecture-reviewer** | SOLID・レイヤード構造・依存方向・セキュリティ境界・可観測性の観点でアーキテクチャをレビューする |
| **code-reviewer** | セキュリティ・パフォーマンス・可読性の観点でコードレビューを実施し LGTM / Request Changes を判定する |
| **code-simplifier** | 変更されたコードを再利用性・品質・効率の観点でレビューし問題を直接修正する |
| **design-reviewer** | SOLID・結合度・凝集度・責任分割の観点でクラス・モジュール設計をレビューする |
| **document-reviewer** | 要件定義書・設計書・仕様書などのソフトウェア開発ドキュメントをレビューし Approved / Needs Revision を判定する |
| **security-reviewer** | OWASP Top 10 に基づく脆弱性パターンを検出し、重要度付きで報告する |
| **test-reviewer** | テストの網羅性・設計・可読性・独立性・アサーション品質を分析し LGTM / Request Changes を判定する |

### 汎用 — 13

| スキル | 概要 |
|--------|------|
| **api-designer** | REST / GraphQL API の設計・ドキュメント生成・バリデーション方針を支援する |
| **brainstorming** | 実装前にユーザーの意図・要件・設計を整理する |
| **ci-cd-configurator** | GitLab CI / Jenkins パイプラインを構築・最適化する |
| **debug-mode** | ランタイムログを使った体系的 printf デバッグ。複数仮説→証拠収集→CONFIRMED 判定で修正する |
| **deep-research** | 複数の情報源を統合し、引用付きで根拠ある深いリサーチを行う |
| **doc-coauthoring** | 仕様書・提案書・設計ドキュメント・RFC・ADR などを 3 ステージのワークフローでユーザーと共同執筆する |
| **domain-modeler** | DDD に基づくドメインモデルを設計し Mermaid classDiagram として出力する |
| **dynamodb-designer** | DynamoDB テーブル設計・インデックス戦略・クエリ最適化を支援する |
| **skill-selector** | ユーザーのタスクを分析し、最適なスキルの組み合わせを選択・推薦するメタスキル |
| **systematic-debugging** | 根本原因を特定してから修正する体系的デバッグ手順を提供する |
| **technical-writer** | README・デベロッパーガイド・チュートリアルなどを読者中心の 5 原則で作成する |
| **ui-designer** | デザインシステムに基づいた UI 実装ガイドラインを提供する |
| **webapp-testing** | Playwright でローカル Web アプリを検証する。画面操作自動化・スクリーンショット・コンソールログ確認を支援する |

### テスト — 1

| スキル | 概要 |
|--------|------|
| **tdd-executing** | Red-Green-Refactor サイクルで TDD を実行し、C1 カバレッジ 100% を達成する |

### 特許・知財 — 2

| スキル | 概要 |
|--------|------|
| **patent-coach** | 特許出願前の構想・整理フェーズ専用。ソクラテス式対話で発明を深掘りし、先行技術調査・新規性確認を支援する |
| **patent-writer** | JPO 様式準拠の特許明細書ドラフトを作成する。三位一体クレーム・AI 発明記載・明細書レビューに対応する |

## インストール

```bash
git clone https://github.com/myorg/agent-skills.git
python agent-skills/install.py
```

コアスキルがユーザー領域（`~/.copilot/skills/`）にコピーされ、ソースリポジトリがレジストリに自動登録される。2回目以降の実行はスキルを上書き更新する（レジストリの既存設定は保持）。

## クイックスタート

### 1. スキルリポジトリを登録する

```
「https://github.com/myorg/agent-skills.git をスキルリポジトリに登録して」
```

### 2. スキルを取得する

```
「スキルをpullして」
```

### 3. スキルを検索する

```
「converter で検索して」        ← ローカルインデックスから即時検索
「最新のスキルを検索して」       ← リモートからインデックスを更新して検索
```

### 4. スキルのバージョンを固定する

```
「docx-converter を今のバージョンに固定して」
「全スキルをロックして」
```

### 5. ワークスペースのスキルを昇格する

```
「ワークスペースのスキルを他のプロジェクトでも使えるようにして」
```

ワークスペース内のスキルをユーザー領域にコピーし、リポジトリに push する。

## scrum-master の使い方

scrum-master は複雑なタスクをスプリント制で段階的に実行するオーケストレーター。
以下のようなフレーズで発動する。

### 発動フレーズ

- 「スクラムして」「スクラム開発して」
- 「スプリントで進めて」
- 「チームで開発して」
- 「タスク分解して実行して」
- 「バックログを作って進めて」
- 「段階的に開発して」
- 「〜を作って〜して」のような複合的な依頼

### 実行フロー

```
Phase 1  スキル探索 ─── 利用可能なスキルを把握
   │
Phase 2  バックログ作成 ─── プロンプトをタスクに分解
   │
Phase 3  スキルギャップ解決 ─── 足りないスキルを作成・改良
   │
Phase 4  スプリントプランニング ─── 3〜5タスクを選出、ユーザー承認
   │
Phase 5  タスク実行 ─── サブエージェントに委譲して並列実行（Wave単位）
   │
Phase 6  レビュー & レトロ ─── sprint-reviewer による評価
   │
Phase 7  進捗レポート ─── 次スプリント / バックログ見直し / 完了
   │
   └──→ Phase 4 に戻る（バックログ残あり）
```

### 使用例

```
ユーザー: 「React + TypeScript でダッシュボードを作って、
           テストも書いて、デプロイ設定もして」

scrum-master:
  1. バックログに分解（コンポーネント作成、API接続、テスト、CI設定 …）
  2. Sprint 1: コンポーネント設計 + API接続（3タスク）
     → ユーザー承認 → サブエージェントが実行 → レビュー
  3. Sprint 2: テスト + デプロイ設定（3タスク）
     → ユーザー承認 → サブエージェントが実行 → レビュー
  4. 最終レポート
```

### ガードレール

- 1スプリント = 3〜5タスク
- スプリント上限 = 5回（超過時はユーザーに確認）
- スキル作成の再試行 = 最大2回
- 各スプリント完了時にユーザー承認を求める

## スキル管理（git-skill-manager）

### 主要操作

| 操作 | フレーズ例 |
|------|-----------|
| リポジトリ登録 | 「リポジトリを登録して」 |
| スキル取得 | 「スキルをpullして」 |
| スキル共有 | 「スキルをpushして」 |
| 検索 | 「スキルを探して」 |
| 有効化/無効化 | 「スキルを有効化/無効化して」 |
| バージョン固定 | 「スキルを固定して」「全スキルをロックして」 |
| 昇格 | 「このスキルを他でも使えるようにして」 |
| 変更履歴生成 | 「スキルの変更履歴を生成して」「CHANGELOGを作って」 |
| プロファイル切替 | 「frontendプロファイルに切り替えて」 |
| **フィードバック記録** | 「[スキル名] のフィードバックを記録して」 |

### フィードバックを記録する

スキルを単体で実行した場合、**エージェントが実行完了後に自動でフィードバックを求める**（`.github/copilot-instructions.md` に定義）。手動で記録することも可能:

```
「react-frontend-coder のフィードバックを記録して」
「ui-designer に ok を付けて」
「systematic-debugging に needs-improvement を記録して。ステップ3が分かりにくかった」
```

verdict の種類:
- `ok` — 問題なく動作した
- `needs-improvement` — 改善の余地がある（具体的な内容を添えると改良時に活用される）
- `broken` — 動作しなかった

フィードバックが蓄積されると git-skill-manager が改良・昇格を自動提案する。

> **scrum-master 経由で実行した場合**: スプリント終了時（Phase 6）にまとめて収集されるため、個別に記録する必要はない。

### 使用頻度による優先度

スキルの使用回数が自動記録され、よく使うスキルほどコンテキストに優先的にロードされる。
基盤スキル（`scrum-master` / `git-skill-manager` とその依存スキル）は使用頻度に関わらず常に最優先。

### ディレクトリ構成

```
~/.copilot/                          # macOS / Linux
%USERPROFILE%\.copilot\              # Windows
  ├── skills/              ← インストール済みスキル
  ├── cache/               ← リポジトリキャッシュ（永続）
  └── skill-registry.json  ← レジストリ（リポジトリ・スキル・プロファイル管理）
```

## ドキュメント

| ファイル | 内容 |
|----------|------|
| [docs/project-overview.md](docs/project-overview.md) | プロジェクト全体像・設計思想・アーキテクチャ |
| [docs/guide-beginners.md](docs/guide-beginners.md) | 初心者ガイド — インストールから最初のスキル利用まで |
| [docs/guide-intermediate.md](docs/guide-intermediate.md) | 中級者ガイド — チーム運用・カスタマイズ・スキル共有 |
| [docs/guide-advanced.md](docs/guide-advanced.md) | 上級者ガイド — 高品質 SKILL.md 設計・マルチエージェント・セキュリティ |
| [docs/designs/node-federation-design.md](docs/designs/node-federation-design.md) | ノードフェデレーション設計 — ローカル改善と中央集約の仕組み |
| [docs/plans/feature-proposals.md](docs/plans/feature-proposals.md) | 機能追加案・ロードマップ |

## 動作環境

- GitHub Copilot (macOS / Windows) または Claude Code
- Git インストール・認証設定済み（SSH 鍵 or credential manager）
- Python 3.10 以上
