# 機能追加案（Feature Proposals）

現在のスキル構成と運用フローを分析した上で、以下の機能追加案を提案する。

---

## 1. 新規スキル案

### 1.1 `api-designer` — API 設計スキル ✅ 実装済み

**概要**: REST / GraphQL API の設計・ドキュメント生成・バリデーションを支援する。

**背景**: 現在 React フロントエンド系スキルは充実しているが、バックエンド API 設計を体系的にガイドするスキルがない。フロントエンドとバックエンドの両輪を揃えることで、フルスタック開発のカバレッジが向上する。

**主な機能**:
- OpenAPI / GraphQL スキーマの設計ガイド
- エンドポイント命名規約・バージョニング戦略の提案
- リクエスト/レスポンス設計のベストプラクティス適用
- エラーハンドリング・ステータスコード設計
- スキーマからモックサーバー生成の手順提供

---

### 1.2 `db-schema-designer` — DB スキーマ設計スキル

**概要**: データベーススキーマの設計・マイグレーション戦略を支援する。

**背景**: 要件定義（requirements-definer）からコード実装へのギャップを埋めるデータモデリング層が不足している。

**主な機能**:
- ER 図のテキスト表現（Mermaid）生成
- 正規化 / 非正規化のトレードオフ分析
- インデックス設計の推奨
- マイグレーションスクリプトのテンプレート生成
- 主要 ORM（Prisma, Drizzle, SQLAlchemy 等）対応

---

### 1.3 `code-reviewer` — コードレビュースキル ✅ 実装済み

**概要**: プルリクエスト単位でコードレビューを実施し、改善提案を出す。

**背景**: sprint-reviewer はスプリント全体の評価を行うが、個別のコード変更に対する詳細なレビューは対象外。コードレビューの品質と一貫性を底上げできる。

**主な機能**:
- diff ベースのレビュー（セキュリティ、パフォーマンス、可読性）
- プロジェクト固有のコーディング規約への準拠チェック
- LGTM / Request Changes の判定と根拠の明示
- codebase-to-skill で生成されたプロジェクトスキルとの連携

---

### 1.4 `test-strategy-planner` — テスト戦略プランナー

**概要**: プロジェクト全体のテスト戦略（単体・結合・E2E）を設計する。

**背景**: react-frontend-unit-tester は React コンポーネントの単体テストに特化しているが、テストピラミッド全体を俯瞰した戦略設計が欠けている。

**主な機能**:
- テストピラミッドに基づくカバレッジ目標の策定
- E2E テストフレームワーク選定ガイド（Playwright, Cypress 等）
- CI/CD パイプラインへのテスト統合設計
- テストデータ管理戦略
- 非機能テスト（パフォーマンス、アクセシビリティ）の計画

---

### 1.5 `ci-cd-configurator` — CI/CD 設定スキル ✅ 実装済み

**概要**: GitHub Actions / GitLab CI 等の CI/CD パイプラインを構築・最適化する。

**背景**: scrum-master でスプリント開発した成果物をデプロイするまでの自動化が現状カバーされていない。

**主な機能**:
- GitHub Actions ワークフロー生成
- ビルド・テスト・デプロイの各ステージ設計
- キャッシュ戦略・並列化による高速化
- 環境変数・シークレット管理のベストプラクティス
- マルチ環境（staging / production）デプロイ設計

---

### 1.6 `refactoring-guide` — リファクタリングガイド

**概要**: 技術的負債の特定とリファクタリング計画を策定する。

**背景**: systematic-debugging は問題の「修正」に焦点を当てているが、コードの「改善」を体系的に行うスキルがない。

**主な機能**:
- コードスメルの検出と分類
- リファクタリングパターンの提案（Extract Method, Move Field 等）
- 影響範囲分析とリスク評価
- 段階的リファクタリング計画の策定
- リファクタリング前後のテスト戦略

---

### 1.7 `technical-writer` — ドキュメント作成スキル ✅ 実装済み（`documentation-writer` から改名）

**概要**: 技術ドキュメント（README、デベロッパーガイド、チュートリアル等）を作成・整備する。

> **注記**: 当初 `documentation-writer` として提案されたが、`technical-writer` という名称で実装された。

**背景**: スキル自体の SKILL.md は skill-creator が対応するが、プロジェクト全般のドキュメント作成を支援するスキルがない。

**主な機能**:
- README テンプレート生成と内容充実
- ADR（Architecture Decision Record）の作成
- API ドキュメント（Swagger UI 連携）生成
- CHANGELOG の自動更新
- コードからのドキュメント抽出と整形

---

### 1.8 `debug-mode` — printfデバッグスキル ✅ 実装済み

**概要**: ランタイムログを使った体系的なprintfデバッグを支援する。

**背景**: `systematic-debugging` は根本原因特定に焦点を当てた汎用デバッグ手順を提供するが、ランタイムログを駆使した仮説検証型のデバッグをより詳細にガイドするスキルが必要だった。両スキルはトリガー条件が異なり、相互補完的に機能する。

**主な機能**:
- 複数仮説の列挙と証拠収集によるCONFIRMED判定フロー
- ログ出力箇所の体系的な追加・確認手順
- Windows / GitHub Copilot 対応

---

### 1.9 `doc-coauthoring` — ドキュメント共同執筆スキル ✅ 実装済み

**概要**: 仕様書・提案書・設計ドキュメント・RFC・ADR などの構造化された文書をユーザーと 3 ステージで共同執筆する。

**背景**: `technical-writer` は既存ドキュメントの作成・整備を担当するが、意思決定ドキュメントや提案書のように「ユーザーと対話しながら内容を固める」用途には向いていない。両スキルを分離することで、それぞれの役割を明確化した。

> **注記**: `anthropics/skills` の `doc-coauthoring` を日本語・Windows・Copilot対応でポーティング（PR #79）。

**主な機能**:
- **Stage 1 コンテキスト収集**: 目的・読者・制約をヒアリング
- **Stage 2 構造化と推敲**: アウトライン生成 → 本文執筆 → ユーザーと反復レビュー
- **Stage 3 読者テスト**: 完成ドキュメントを想定読者視点でレビューし改善提案
- Windows / GitHub Copilot 対応

---

### 1.10 `architecture-reviewer` — アーキテクチャレビュースキル ✅ 実装済み

**概要**: SOLID・レイヤードアーキテクチャ・依存方向・モジュール境界・セキュリティ境界・可観測性の観点でアーキテクチャをレビューする。

**背景**: `code-reviewer` が個別のコード変更をレビューするのに対し、システム全体の構造的健全性を評価するスキルが不足していた。

---

### 1.11 `design-reviewer` — クラス・モジュール設計レビュースキル ✅ 実装済み

**概要**: SOLID・結合度・凝集度・依存方向・責任分割を評価し LGTM / Request Changes を判定する。

**背景**: `architecture-reviewer` がシステム全体の構造を見るのに対し、個別のクラスやモジュール単位での設計品質をレビューする専門スキルが必要だった。

---

### 1.12 `document-reviewer` — ドキュメントレビュースキル ✅ 実装済み

**概要**: 要件定義書・設計書・仕様書などのソフトウェア開発ドキュメントをレビューし、完全性・明確性・一貫性・実現可能性・テスト容易性の観点で Approved / Needs Revision を判定する。

**背景**: `code-reviewer` がコードをレビューするのに対し、ドキュメントの品質を評価する専門スキルが不足していた。

---

### 1.13 `test-reviewer` — テストコードレビュースキル ✅ 実装済み

**概要**: テストの網羅性・設計・可読性・独立性・アサーション品質を分析し LGTM / Request Changes を判定する。

**背景**: `code-reviewer` はプロダクションコードのレビューに特化しており、テストコード特有の品質観点（網羅性・独立性・テスト粒度等）を専門的に評価するスキルが必要だった。

---

### 1.14 `tdd-executing` — テスト駆動開発スキル ✅ 実装済み

**概要**: Red-Green-Refactor サイクルで TDD を実行し、C1 カバレッジ 100% を達成する。実装・テストは言語固有スキルに委譲する。

**背景**: `react-frontend-unit-tester` は React 限定でかつ実装後のテスト追加に向いている。言語横断・テストファーストの開発フローをガイドするスキルが必要だった。

---

### 1.15 `skill-selector` — スキル選択メタスキル ✅ 実装済み

**概要**: ユーザーのタスクを分析し、利用可能なスキルの中から最適な組み合わせを選択・推薦するメタスキル。

**背景**: スキル数の増加に伴い、ユーザーがどのスキルを使えばよいか判断しにくくなっていた。`scrum-master` の Phase 1 スキル探索を補完し、複合タスクへの対応を強化する目的で実装。

---

### 1.16 `patent-coach` — 特許出願前コーチングスキル ✅ 実装済み

**概要**: 特許出願前の構想・整理フェーズ専用。ソクラテス式対話で発明を深掘りし、先行技術調査・新規性確認を支援する。

**背景**: ソフトウェア・AI 発明の特許出願ニーズに対応するため追加。明細書作成（`patent-writer`）との役割分離を明確化。

---

### 1.17 `patent-writer` — 特許明細書作成スキル ✅ 実装済み

**概要**: 発明内容が固まった後の書類作成・編集フェーズ専用。JPO 様式準拠の特許明細書ドラフトを作成し、三位一体クレーム・AI 発明記載・明細書レビューに対応。

**背景**: `patent-coach` でコーチングした発明を、実際の出願書類に落とし込む後工程スキルとして実装。

---

## 2. フレームワーク機能強化案

### 2.1 スキル依存関係の明示的管理

**現状**: スキル間の依存はスクラムマスターが暗黙的に解決している。

**提案**: SKILL.md の YAML フロントマターに `depends_on` フィールドを追加し、依存関係を明示化する。

```yaml
---
name: react-frontend-unit-tester
description: React コンポーネントのテストを作成する
depends_on:
  - react-frontend-coder
  - react-best-practices
---
```

**効果**:
- スキルの自動ロード順序の最適化
- 依存スキル未インストール時の警告表示
- スキルグラフの可視化

---

### 2.2 スキルのバージョニング強化（セマンティックバージョニング）

**現状**: git コミットハッシュベースのバージョン管理。

**提案**: SKILL.md に `version` フィールドを追加し、セマンティックバージョニングを導入する。

```yaml
---
name: react-frontend-coder
version: 1.2.0
min_framework_version: 2.0.0
---
```

**効果**:
- 破壊的変更の明確化（メジャーバージョン）
- フレームワークとスキルの互換性管理
- `git-skill-manager` によるアップデート通知（「v2.0.0 が利用可能です」）

---

### 2.3 スキル実行メトリクスの収集・可視化

**現状**: 使用回数と verdict（ok / needs-improvement / broken）のみ記録。

**提案**: 以下のメトリクスを追加収集する。

- **実行時間**: スキル実行にかかった時間
- **サブエージェント呼び出し回数**: 1タスクあたりの呼び出し数
- **成功率の推移**: 時系列でのverdict比率
- **関連スキルの共起**: どのスキルが一緒に使われやすいか

**効果**:
- スキルのパフォーマンスボトルネック特定
- スキルセットの最適な組み合わせ推薦
- 改善効果の定量的な測定

---

### 2.4 スキルのコンポジション（合成）機能

**現状**: スキルは個別に実行され、組み合わせは scrum-master が管理する。

**提案**: 複数スキルを合成して新しいワークフローを定義できる `composite-skill` フォーマットを導入する。

```yaml
---
name: fullstack-feature
type: composite
steps:
  - skill: requirements-definer
    output: requirements.md
  - skill: api-designer
    input: requirements.md
    output: api-spec.yaml
  - skill: react-frontend-coder
    input: api-spec.yaml
  - skill: react-frontend-unit-tester
---
```

**効果**:
- よく使うワークフローの再利用性向上
- scrum-master を介さない軽量なオーケストレーション
- チーム固有の開発フローのテンプレート化

---

### 2.5 マルチ言語スキルテンプレートの拡充

**現状**: React + TypeScript に特化したスキルのみ。

**提案**: 以下の言語 / フレームワーク向けテンプレートスキルを追加する。

| 優先度 | 言語/FW | スキル名案 |
|--------|---------|-----------|
| 高 | Python + FastAPI | `python-backend-coder` |
| 高 | Next.js (App Router) | `nextjs-fullstack-coder` |
| 中 | Go + Chi/Echo | `go-backend-coder` |
| 中 | Vue.js 3 | `vue-frontend-coder` |
| 低 | Rust + Axum | `rust-backend-coder` |

**効果**:
- プロジェクトの技術スタックに応じたスキルセット提供
- 新規プロジェクト立ち上げの高速化

---

### 2.6 スキルのサンドボックス実行環境

**現状**: スキル内のスクリプトはホスト環境で直接実行される。

**提案**: 外部から取得したスキルのスクリプトを隔離環境（Docker コンテナ or Python venv）で実行するオプションを追加する。

**効果**:
- 外部スキルの安全な試用（skill-recruiter との連携強化）
- 環境汚染の防止
- 再現可能な実行環境の保証

---

### 2.7 スキルマーケットプレイス連携

**現状**: スキルの共有は git リポジトリ経由のみ。

**提案**: 中央レジストリ（npm レジストリ風）を導入し、スキルの検索・公開・インストールを簡素化する。

```
「マーケットプレイスで "API testing" を検索して」
「skill-xyz をインストールして」       ← レジストリから直接取得
「このスキルをマーケットプレイスに公開して」
```

**効果**:
- スキルのディスカバラビリティ向上
- コミュニティによるスキルエコシステムの成長
- 人気度・レビューに基づくスキル選定

---

## 3. ノードフェデレーション機能（各ノードでの改善を中央へ選択的集約）✅ 実装済み

中央リポジトリを信頼の集積点として残しつつ、各ノード（開発環境）での
ローカル改善を可視化し、価値あるものだけを中央へ貢献できる仕組み。

詳細設計: [docs/node-federation-design.md](./node-federation-design.md)

---

### 3.1 ノードアイデンティティ管理（`node_identity.py`）✅ 実装済み

**現状**: 誰の環境からのスキル改善かを追跡できない。

**実装**: `registry.json` に `node.id` フィールドを追加。環境ごとに一意IDを付与。

```bash
python node_identity.py init --name "tokyo-team"
python node_identity.py show
```

**効果**: 中央へのPR時に「node-abc123 (tokyo-team) からの貢献」として追跡可能。

---

### 3.2 ローカル差分追跡（`delta_tracker.py`）✅ 実装済み

**現状**: ノードでスキルを変更しても、中央との差分を追跡する手段がない。

**実装**: SKILL.md のハッシュ比較で `lineage.local_modified` を自動検出。

```bash
python delta_tracker.py                      # 全スキルをスキャン
python delta_tracker.py --note my-skill "RSC対応を追加"
```

**効果**: 「このスキルはローカルで改善済み → 中央への貢献候補」を自動特定。

---

### 3.3 昇格ポリシーエンジン（`promotion_policy.py`）✅ 実装済み

**現状**: 「ok が3回で昇格候補」という単純な閾値のみ。設定不可・自動通知なし。

**実装**: ポリシーを `registry.json` で設定可能にし、複合条件で昇格適性を自動判定。

```bash
python promotion_policy.py                   # 全スキルを評価
python promotion_policy.py --queue           # 適格スキルを貢献キューへ追加
python promotion_policy.py --set-policy min_ok_count=5
```

**昇格条件（デフォルト）**:
- ok 件数 ≥ 3
- 問題率 ≤ 10%
- ローカルで何らかの改善あり（`local_modified: true`）
- 未解決問題なし（`pending_refinement: false`）

---

### 3.4 選択的同期ポリシー（`sync_policy` in `registry.json`）✅ 実装済み

**現状**: pull 時にローカル改善が中央の更新で上書きされてしまう。

**実装**: `sync_policy.protect_local_modified: true` でローカル改善を保護。

```json
{
  "sync_policy": {
    "auto_accept_patch": true,
    "auto_accept_minor": false,
    "protect_local_modified": true
  }
}
```

**効果**: ローカル改善済みスキルは中央の更新があっても上書きされず通知のみ。

---

### 3.5 貢献キュー（`contribution_queue` in `registry.json`）✅ 実装済み

**現状**: 昇格は「評価→即push」の二択。レビュー待ち機構がない。

**実装**: `contribution_queue` フィールドで昇格候補をステージング。

```json
{
  "contribution_queue": [
    {
      "skill_name": "react-frontend-coder",
      "queued_at": "2026-02-27T10:00:00Z",
      "reason": "ok:5件; ローカル改善あり",
      "status": "pending_review",
      "node_id": "node-abc123"
    }
  ]
}
```

**ステータス**: `pending_review` → `merged` / `rejected`

---

### 3.6 スキル系譜（lineage）追跡 ✅ 実装済み

**現状**: スキルの出所（`source_repo`）はあるが、「どの版から派生したか」の記録がない。

**実装**: `installed_skills[].lineage` フィールドで派生元を記録。

```json
{
  "lineage": {
    "origin_repo": "team-skills",
    "origin_commit": "a1b2c3d",
    "origin_version": "1.2.0",
    "local_modified": true,
    "diverged_at": "2026-02-20T00:00:00Z",
    "local_changes_summary": "RSC対応を追加"
  }
}
```

---

### 3.7 セマンティックバージョニング（SKILL.md への `version` 追加）✅ 実装済み

**現状**: コミットハッシュのみでバージョン管理。「新しいか古いか」が直感的でない。

**実装**: SKILL.md フロントマターに `version` フィールドを追加。

```yaml
---
name: react-frontend-coder
description: "..."
version: 1.3.0
---
```

**レジストリ**: `version` (ローカル版) と `central_version` (中央版) を比較し `version_ahead` を設定。

---

## 4. 優先度マトリクス

| 提案 | 実装コスト | ユーザー価値 | 推奨優先度 | ステータス |
|------|-----------|-------------|-----------|-----------|
| **ノードフェデレーション（全体）** | 中 | 高 | **★★★** | ✅ 実装済み |
| code-reviewer | 中 | 高 | **★★★** | ✅ 実装済み |
| スキル依存関係管理 | 低 | 高 | **★★★** | 未実装 |
| スキルコンポジション | 中 | 高 | **★★★** | 未実装 |
| ci-cd-configurator | 中 | 高 | **★★★** | ✅ 実装済み |
| api-designer | 中 | 中 | **★★☆** | ✅ 実装済み |
| test-strategy-planner | 低 | 中 | **★★☆** | 未実装 |
| 実行メトリクス収集 | 中 | 中 | **★★☆** | 未実装 |
| セマンティックバージョニング | 低 | 中 | **★★☆** | ✅ 実装済み |
| db-schema-designer | 中 | 中 | **★★☆** | 未実装 |
| refactoring-guide | 中 | 中 | **★★☆** | ✅ `code-simplifier` として実装 |
| documentation-writer | 低 | 中 | **★★☆** | ✅ `technical-writer` として実装 |
| debug-mode | 低 | 中 | **★★☆** | ✅ 実装済み（`systematic-debugging` と相互補完） |
| doc-coauthoring | 低 | 中 | **★★☆** | ✅ 実装済み（`technical-writer` と役割分離） |
| architecture-reviewer | 低 | 高 | **★★☆** | ✅ 実装済み |
| design-reviewer | 低 | 中 | **★★☆** | ✅ 実装済み |
| document-reviewer | 低 | 中 | **★★☆** | ✅ 実装済み |
| test-reviewer | 低 | 中 | **★★☆** | ✅ 実装済み |
| security-reviewer | 低 | 高 | **★★☆** | ✅ 実装済み |
| tdd-executing | 低 | 中 | **★★☆** | ✅ 実装済み |
| skill-selector | 低 | 高 | **★★☆** | ✅ 実装済み |
| patent-coach | 低 | 中 | **★★☆** | ✅ 実装済み |
| patent-writer | 低 | 中 | **★★☆** | ✅ 実装済み |
| マルチ言語テンプレート | 高 | 高 | **★★☆** | 未実装 |
| サンドボックス実行環境 | 高 | 中 | **★☆☆** | 未実装 |
| マーケットプレイス | 高 | 高 | **★☆☆** | 未実装 |

> 推奨: まず ★★★ の未実装項目（スキル依存関係管理・スキルコンポジション）に着手し、フレームワークの基盤を強化した上で ★★☆ の未実装項目に進む。
>
> **実装済みスキル数**: 25/31 提案が実装完了（ノードフェデレーション含む）。
