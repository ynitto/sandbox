# ワークスペーススキル 改善案・拡張案レポート

> **作成日**: 2026-03-10  
> **対象**: `.github/skills/` 配下の 36 スキル  
> **関連**: `feature-proposals.md`、`2026-03-08-skill-ideas-design.md`、`ltm-use-v4-design.md`

---

## 0. 全体像

### スキル一覧（36スキル・8カテゴリ）

| カテゴリ | スキル数 | スキル名 |
|---------|---------|---------|
| **オーケストレーション** | 2 | scrum-master (v1.4.0), sprint-reviewer (v1.0.0) |
| **メタ／管理** | 8 | git-skill-manager (v1.0.1), skill-recruiter (v1.0.0), skill-creator (v1.0.0), skill-evaluator (v1.0.1), skill-selector (v1.0.0), generating-skills-from-copilot-logs (v1.0.0), ltm-use (v4.0.0), codebase-to-skill (v1.0.0) |
| **要件／設計** | 6 | requirements-definer (v2.0.0), brainstorming (v2.0.0), domain-modeler (v1.0.0), api-designer (v1.0.0), ui-designer (v1.0.0), doc-coauthoring (v1.0.0) |
| **実装** | 5 | react-frontend-coder (v1.0.0), react-frontend-unit-tester (v1.0.0), tdd-executing (v1.0.1), ci-cd-configurator (v1.0.0), webapp-testing (v1.0.0) |
| **レビュー** | 8 | code-reviewer (v2.0.0), code-simplifier (v1.0.0), architecture-reviewer (v1.2.0), design-reviewer (v1.0.0), document-reviewer (v2.0.0), security-reviewer (v1.0.0), test-reviewer (v1.2.0), sprint-reviewer (※オーケストレーションと兼務) |
| **デバッグ** | 2 | debug-mode (v1.0.0), systematic-debugging (v1.0.0) |
| **調査／文書** | 4 | deep-research (v1.0.0), technical-writer (v1.0.1), patent-coach (v1.0.0), patent-writer (v2.2.0) |
| **データ** | 1 | dynamodb-designer (v1.0.0) |
| **参照** | 1 | react-best-practices (v1.0.0) |

### スキル間の依存・委譲ネットワーク

```
開発ライフサイクル（上流→下流）:
  brainstorming → requirements-definer → domain-modeler → api-designer
                                                       ↘ ui-designer
                                                         ↓
                                    react-frontend-coder → react-frontend-unit-tester
                                                         ↓
                                    tdd-executing (C1 100% ゲート)
                                                         ↓
                                    ci-cd-configurator → webapp-testing

レビューパイプライン:
  code-reviewer ──→ code-simplifier（修正実施）
       ├─ 委譲 → security-reviewer（セキュリティ深掘り）
       ├─ 委譲 → architecture-reviewer（システム構造）
       └─ 委譲 → design-reviewer（クラス設計）
  test-reviewer（テストコード専用）
  document-reviewer（ドキュメント専用）

オーケストレーション:
  scrum-master ─── Phase 1: skill-selector
              ├── Phase 2: requirements-definer
              ├── Phase 3: skill-creator（ギャップ補完）
              ├── Phase 5: 実装スキル群（Wave 単位委譲）
              ├── Phase 6: sprint-reviewer + skill-evaluator
              └── 全 Phase: ltm-use（記憶の save/recall 必須）

メタスキル:
  git-skill-manager ← skill-recruiter（外部取得）
                    ← skill-evaluator（品質評価）
                    ← generating-skills-from-copilot-logs（自動発見）
  skill-creator ← codebase-to-skill（既存コードのスキル化）
```

### 構造統計

| 指標 | 値 |
|-----|-----|
| バージョン v1.0.0 | 22/36 (61%) |
| バージョン v1.x.x（v1.0.0以外） | 7/36 (19%) |
| バージョン v2.0+ | 6/36 (17%) |
| バージョン未記載 | 1/36 (3%) |
| scripts/ あり | 17/36 (47%) |
| references/ あり | 18/36 (50%) |
| 明示的な委譲ルールあり | 8/36 (22%) |

---

## 1. 構造的課題（フレームワークレベル）

### 1.1 スキル依存関係が暗黙的 — **優先度: 高**

**現状**: 36スキル中、他スキルへの委譲を SKILL.md 本文内で記述しているのは約8個（scrum-master, code-reviewer, design-reviewer, patent-coach, tdd-executing 等）。だが YAML フロントマターの `depends_on` として構造化されておらず、**機械的に解決できない**。

**実際の暗黙依存の例**:

| スキル | 暗黙の前提スキル | SKILL.md での言及 |
|--------|----------------|-----------------|
| react-frontend-unit-tester | react-frontend-coder | 「実装完了後のテスト投入」とあるが depends_on なし |
| tdd-executing | react-frontend-unit-tester 等 | 「言語固有スキルに委譲」とあるが動的選択 |
| test-reviewer | tdd-executing | テスト品質評価だが連携なし |
| api-designer | requirements-definer, domain-modeler | 入力チェックリストで暗示だが依存宣言なし |
| ci-cd-configurator | react-frontend-coder 等 | ビルド対象が存在する前提だが未宣言 |

**影響**: scrum-master 経由でなく単独実行した場合、前提スキルの成果物がなく失敗する。

**改善案**: feature-proposals §2.1 の `depends_on` / `recommends` を全スキルに適用。

```yaml
metadata:
  depends_on:
    - name: react-frontend-coder
      reason: "テスト対象のコンポーネントが存在する前提"
  recommends:
    - name: code-reviewer
      reason: "テスト完了後の品質確認"
```

---

### 1.2 レビュースキル 7 個の責務境界が曖昧 — **優先度: 高**

**現状**: レビュー系 7 スキルが並存し、一部観点が重複している。

各スキルの**実際のレビュー観点**（SKILL.md から抽出）:

| 観点 | code-reviewer | code-simplifier | architecture-reviewer | design-reviewer | security-reviewer | test-reviewer | document-reviewer |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 正確性・ロジック | ● | | | | | | |
| 可読性・命名 | ● | ● | | | | ● | |
| SRP | ● | ● | ● | ● | | | |
| セキュリティ | ● | | | | ● | | |
| パフォーマンス | ● | ● | | | | | |
| 並行性 | ● | | | | | | |
| エラーハンドリング | ● | | | | | | |
| 型安全性 | ● | | | | | | |
| AI生成コード検出 | ● | | | | | | |
| DRY / 重複排除 | | ● | | | | | |
| 依存方向 | | | ● | ● | | | |
| レイヤー境界 | | | ● | | | | |
| SOLID（モジュール） | | | ● | ● | | | |
| 結合度・凝集度 | | | | ● | | | |
| God Class 検出 | | | | ● | | | |
| レジリエンス | | | ● | | | | |
| 可観測性 | | | ● | | | | |
| OWASP Top 10 | | | | | ● | | |
| 認証・認可 | | | | | ● | | |
| テスト網羅性 | | | | | | ● | |
| テスト独立性 | | | | | | ● | |
| 非同期テスト | | | | | | ● | |
| 文書完全性 | | | | | | | ● |
| 文書明確性 | | | | | | | ● |
| 要件トレーサビリティ | | | | | | | ● |

**重複リスクの実態**:

| 対象ペア | 重複する観点 | 分離基準 |
|---------|-------------|---------|
| code-reviewer ⟷ code-simplifier | 可読性、SRP、パフォーマンス | reviewer=指摘のみ、simplifier=修正実施。**ただし code-simplifier も「レビュー」を名乗っており混同しやすい** |
| code-reviewer ⟷ security-reviewer | セキュリティ全般 | reviewer=浅い検出、security-reviewer=OWASP深掘り。**code-reviewer が11次元目でセキュリティを含むため boundaries が不明瞭** |
| architecture-reviewer ⟷ design-reviewer | SOLID、依存方向 | architecture=システム／モジュール俯瞰、design=クラス単位。**粒度の境界が暗黙的**（design-reviewer は「委譲先: architecture-reviewer」と記載あり） |

**改善案**:
1. 各レビュースキルに **"Out of Scope / 委譲先"セクション** を追加
2. code-reviewer のセキュリティ観点を「検出のみ・深掘りは security-reviewer へ委譲」と明示
3. skill-selector の taxonomy にレビュスキルの選択ガイドを追加

---

### 1.3 実装スキルが React/TypeScript に偏重 — **優先度: 高**

**現状**: 実装系スキル 5 個の言語・フレームワーク分布:

| スキル | 言語/FW | 対象 |
|--------|---------|------|
| react-frontend-coder | React + TypeScript + Vite + Zustand | フロントエンド |
| react-frontend-unit-tester | Jest/Vitest + RTL | フロントテスト |
| tdd-executing | 多言語（TS/Python/Go/Java/Rust/C#） | テスト駆動 |
| ci-cd-configurator | GitLab CI / Jenkins + **AWS限定** | CI/CD |
| webapp-testing | Playwright (Python) | E2E テスト |

**カバレッジの空白**:
- バックエンド実装スキルが **ゼロ**（Python/Go/Java いずれもなし）
- フロントエンドも React + Vite + Zustand に固定。Next.js App Router 未対応
- CI/CD が GitHub Actions 未対応（GitLab CI / Jenkins のみ）
- CI/CD が AWS 限定（GCP/Azure 未対応）

---

### 1.4 スクリプト実装の不均衡 — **優先度: 中**

| 区分 | 数 | 代表例 |
|------|---|-------|
| 豊富なスクリプト群 | 4 | ltm-use (9本), git-skill-manager (5本+), scrum-master (2本), skill-evaluator (2本) |
| 最小限のスクリプト | 8 | skill-recruiter (1本), webapp-testing (1本), ui-designer (1本) 等 |
| スクリプトなし | 25 | code-reviewer, security-reviewer, requirements-definer 等 |

**スクリプト化で信頼性が向上するスキルの候補**:

| スキル | スクリプト化したい処理 | 根拠 |
|--------|---------------------|------|
| requirements-definer | `requirements.json` のスキーマバリデーション | 出力が JSON で構造化されておりバリデーション可能 |
| security-reviewer | grep/AST ベースの脆弱性パターン検出 | OWASP パターンは定型的でスクリプト化の効果が高い |
| code-reviewer | diff 解析 + PR コメントフォーマッタ | GitHub PR コメント形式出力を支援 |
| api-designer | OpenAPI スキーマバリデーション（`openapi-generator validate`） | 生成した YAML の文法チェックを自動化 |

---

### 1.5 バージョン管理・メタデータの不統一 — **優先度: 低**

| 問題 | 該当スキル |
|------|-----------|
| バージョン未記載 | ~~design-reviewer, skill-selector~~ → **解消済み**（全スキルに `version` 追記完了） |
| semver ポリシー未定義 | 全体（1.0.0→2.0.0 の基準が不明） |
| description が YAML フロントマターで multiline 非対応 | skill-evaluator が `DESC_MULTILINE` エラーとして検出するが、修正ガイダンスなし |

---

## 2. 既存スキルの改善案

### 2.1 `scrum-master` v1.4 → v2.0 — 並列タスク実行 ★★★

**現状**: Phase 5 の Wave 実行は対応しているが、タスク間の依存関係を分析して DAG ベースで並列化する機構はない。

**詳細な問題点**:
- 「鉄則」として Phase 順序が固定的（Phase 1→7 を必ず順守）
- Wave 内のタスクは並列可能だが、Wave 分割自体がスクラムマスターの判断に依存
- plan.json のスキーマに依存関係フィールドがない

**改善案**:
- plan.json に `depends_on: [task_id]` フィールド追加
- Phase 4（スプリント計画）で依存 DAG を構築し、クリティカルパスを可視化
- Phase 5 で DAG に基づく自動 Wave 分割

---

### 2.2 `ltm-use` v4 — 類似記憶推薦の実装完了 ★★★

**現状**: v4.0.0 の SKILL.md でハイブリッドランキング（TF-IDF + cosine similarity）が記述済み。設計書（`ltm-use-v4-design.md`）も Draft で存在。

**残課題**:
- `similarity.py` の実装完了確認（設計書にコード記載あり、統合テスト未確認）
- `auto_tagger.py` の精度検証（日英混合トークナイゼーション）
- cleanup の自動スケジューラ実装

---

### 2.3 `ci-cd-configurator` — GitHub Actions + 複数クラウド対応 ★★★

**現状の制約**（SKILL.md から正確な引用）:
- CI: GitLab CI / Jenkins のみ
- CD: **「AWS に限定」** と明記

**改善案**:
- GitHub Actions ワークフロー（`.github/workflows/*.yml`）生成を追加
- GCP Cloud Run / Azure Container Apps 対応を段階的に追加
- 少なくとも GitHub Actions は必須（ワークスペース自体が GitHub ホスト）

---

### 2.4 `code-reviewer` v2.0 — さらなる深化 ★★☆

**v2.0.0 での実装済み機能**:
- AI 生成コード特有のアンチパターン検出（11次元目）
- `.copilot-review-rules.md` からのカスタムルール読み込み
- GitHub PR コメント形式出力

**残る改善余地**:
- セキュリティ観点の **委譲境界** 明確化（security-reviewer との分担ライン）
- diff 解析スクリプトの追加（現在は LLM 判断のみ）
- レビュー結果の構造化出力（JSON フォーマット）で sprint-reviewer が集計しやすくする

---

### 2.5 `git-skill-manager` v1.0 → v2.0 — ヘルスチェック + 依存グラフ ★★☆

**改善案**:
- `--graph` オプション: インストール済みスキルの依存グラフを Mermaid で出力
- `--health` オプション: 各スキルの「使用頻度・最終使用日・破損有無」をチェック
- `depends_on` フィールド解析による自動依存解決（install 時に依存スキルも取得）

---

### 2.6 `brainstorming` v2.0 → v3.0 — ltm-use 連携強化 ★☆☆

**v2.0.0 で実装済み**: トレードオフマトリクス、Decision Record、スキル重複検出

**次の改善**:
- Decision Record を `ltm-use` に自動保存（同テーマ再検討時に過去の決定をリコール）
- 過去のブレスト結果を recall して「前回の結論」を提示

---

### 2.7 `debug-mode` + `systematic-debugging` — 連携の明文化 ★☆☆

**現状**: 両スキルの関係は「補完的」とされるが、呼び出し順序の指針がない。

| スキル | フォーカス | タイミング |
|--------|----------|----------|
| systematic-debugging | 静的分析・根本原因特定・3-strike ルール | まず最初に使う |
| debug-mode | ランタイムログ・仮説検証・NDJSON 出力 | 静的分析で特定できない場合に使う |

**改善案**: 両スキルの SKILL.md に「組み合わせフロー」セクションを追加。

---

## 3. 新規スキル案

### 3.1 既存計画の未実装案（実態を踏まえた優先度再評価）

| スキル名 | 概要 | 優先度 | 根拠 |
|---------|------|--------|------|
| `db-schema-designer` | RDBMS スキーマ設計・ER図・マイグレーション | ★★★ | dynamodb-designer が NoSQL 限定。RDBMS の空白は大きい |
| `python-backend-coder` | FastAPI/Django バックエンド実装 | ★★★ | 実装スキルが React フロントのみ。バックエンドが完全空白 |
| `test-strategy-planner` | テストピラミッド全体の戦略設計 | ★★★ | react-frontend-unit-tester は単体テスト限定。結合/E2E/非機能の戦略なし |
| `nextjs-fullstack-coder` | Next.js App Router フルスタック実装 | ★★☆ | react-frontend-coder が Vite+Zustand 固定。Next.js 非対応 |
| `refactoring-guide` | 技術的負債特定・リファクタリング計画 | ★★☆ | code-simplifier は実施のみ。大規模リファクタの計画策定なし |
| `performance-profiler` | ボトルネック特定・Core Web Vitals 改善 | ★★☆ | react-best-practices はルール集。実測→改善のフロー未対応 |
| `incident-responder` | 本番障害対応・RCA・ポストモーテム | ★★☆ | debug-mode/systematic-debugging は開発環境限定。本番障害フロー未対応 |
| `migration-planner` | 技術移行計画（Strangler Fig 等） | ★☆☆ | 需要はあるが発動頻度が低い |
| `accessibility-auditor` | WCAG 2.1 AA 準拠監査 | ★☆☆ | ui-designer が最優先でアクセシビリティをカバー。深掘り需要は限定的 |
| `i18n-localizer` | 国際化・多言語対応 | ★☆☆ | ニッチ需要 |

> **注**: `security-auditor` は `security-reviewer` (v1.0.0) として実装済み。2026-03-08 計画時点では未実装だったが現在は解決済み。

### 3.2 新たに発見した空白領域

精読の結果、既存計画にない空白を追加提案する。

| スキル名 | 概要 | 優先度 | 根拠 |
|---------|------|--------|------|
| `go-backend-coder` | Go バックエンド実装（Gin/Echo/net/http） | ★★☆ | ci-cd-configurator が Go 対応だが実装スキルなし |
| `infra-as-code` | Terraform / CDK / Pulumi | ★★☆ | ci-cd がデプロイまでカバーするがインフラ定義は対象外 |
| `openapi-validator` | OpenAPI スキーマの自動検証・モック生成 | ★★☆ | api-designer の出力検証を自動化（後工程スキル） |
| `data-pipeline-designer` | ETL/ELT 設計（Airflow, dbt, Glue） | ★☆☆ | データエンジニアリング領域が完全空白 |
| `monitoring-designer` | オブザーバビリティ設計 | ★☆☆ | architecture-reviewer が可観測性をレビューするが、設計自体を支援するスキルなし |

---

## 4. スキルコンポジション（ワークフロー横断）

### 4.1 定義済みパイプラインの不足 — **優先度: 高**

**現状**: scrum-master が Phase 5 で動的にスキルを組み合わせるが、**定型パイプラインのテンプレート**がない。skill-selector の `combinations.md` に部分的な記載はあるが、エンドツーエンドの完全なチェーンは未定義。

**改善案**: composite スキルフォーマット（2026-03-08設計 §3.2）を実装し、以下をプリセット:

```yaml
# fullstack-feature-workflow
steps:
  - skill: requirements-definer
    output: requirements.json
  - skill: [api-designer, ui-designer]  # 並列
    input: requirements.json
  - skill: react-frontend-coder
    input: api-spec + ui-design
  - skill: react-frontend-unit-tester
  - skill: [code-reviewer, security-reviewer, test-reviewer]  # 並列
  - skill: sprint-reviewer

# quality-gate-workflow
steps:
  - skill: code-reviewer
  - skill: code-simplifier  # Request Changes 時のみ
  - skill: [architecture-reviewer, design-reviewer, security-reviewer]  # 並列
  - skill: test-reviewer

# debug-workflow
steps:
  - skill: systematic-debugging  # 根本原因特定
  - skill: debug-mode            # ランタイム証拠収集（必要時のみ）
  - skill: <implementation-skill> # 修正実施
  - skill: test-reviewer          # 修正検証
```

### 4.2 メトリクス収集・可視化 — **優先度: 中**

**現状**（project-overview §5 より）:
- フィードバック記録（ok / needs-improvement / broken）は動作
- 集計基盤・可視化なし

**改善案**:
- git-skill-manager に `metrics` コマンドを拡充（現在は基本統計のみ）
- スキル別の ok 率・実行回数・最終使用日のダッシュボード出力（Mermaid / JSON）
- 閾値ベースのアラート（ok 率 50% 以下のスキルを自動フラグ）

### 4.3 scrum-master ↔ ltm-use の記憶活用パターン — **優先度: 中**

**現状**: scrum-master は ltm-use を「必須コアスキル」と宣言し Phase 全体で利用。だが **具体的な save/recall のタイミングが Phase ごとに異なり、標準化パターンがない**。

**改善案**: 「スプリント記憶テンプレート」を定義
```
Phase 2 recall: "前回の類似要件の決定事項"
Phase 5 save:   "実装で発見した技術的知見"
Phase 6 save:   "レトロスペクティブの keep/problem/try"
Phase 7 promote: "チームに共有すべき汎用的知見"
```

---

## 5. スキル個別の品質課題

### 5.1 SKILL.md が長大すぎるスキル

| スキル | 推定行数 | 問題 |
|--------|---------|------|
| patent-writer | 600行+ | JPO 様式の詳細が膨大。references/ への分割は進んでいるが本体も長い |
| patent-coach | 600行+ | ソクラテス式質問テンプレート・先行技術調査手順が内包 |
| scrum-master | 400行+ | 7 Phase の詳細は references/ に委譲済みだが、鉄則・制約の列挙が長い |
| ui-designer | 400行+ | 8 カテゴリの検索パラメータ・優先順位ルールが本体に内包 |

**改善案**: `references/` への追加分割。特に patent-coach/writer はルール・テンプレート部を完全に外部化可能。

### 5.2 トリガー条件の重複

複数スキルが同じトリガーフレーズで発動するリスク:

| トリガーフレーズ | 反応するスキル | 調停者 |
|---------------|-------------|--------|
| 「設計をレビューして」 | architecture-reviewer, design-reviewer | skill-selector が粒度で判定すべきだが明示なし |
| 「セキュリティをチェックして」 | code-reviewer (部分), security-reviewer | code-reviewer は浅い検出のみ→委譲ルールはあるがトリガーレベルで未分離 |
| 「パフォーマンスを改善して」 | code-simplifier, react-best-practices | 前者は修正、後者は参照。使い分けがトリガーで区別不能 |

**改善案**: skill-selector のトリガー仲裁ロジックに上記の「曖昧トリガー解決マトリクス」を追加。

### 5.3 プラットフォーム制約の明示不足

| スキル | 制約 | SKILL.md での明示度 |
|--------|------|-----------------|
| ci-cd-configurator | AWS 限定 | ✅ 明示 |
| dynamodb-designer | AWS DynamoDB 限定 | ✅ 明示 |
| react-frontend-coder | Vite + Zustand 限定 | ⚠️ 暗黙的（タイトルに React とあるがスタック固定は本文のみ） |
| ci-cd-configurator | GitLab CI / Jenkins 限定 | ⚠️ GitHub Actions 非対応が目立たない |

---

## 6. 推奨着手順序

```
Phase 1 — 基盤強化（即効性・低コスト）
  ├─ 全スキルに depends_on / recommends フィールド追加
  ├─ レビュースキル群に Out of Scope セクション追加
  ├─ skill-selector に曖昧トリガー解決マトリクス追加
  ├─ ~~design-reviewer / skill-selector にバージョン番号追加~~ → **完了**（skill-selector v1.0.0 追記）
  └─ ci-cd-configurator に GitHub Actions 対応追加

Phase 2 — 高優先度の拡張・新規
  ├─ python-backend-coder 新規作成（バックエンド空白の解消）
  ├─ db-schema-designer 新規作成（RDBMS 設計の空白解消）
  ├─ test-strategy-planner 新規作成（テスト戦略の俯瞰）
  ├─ scrum-master v2.0（DAG ベース並列実行）
  └─ ltm-use v4 実装完了確認（similarity.py, auto_tagger.py）

Phase 3 — カバレッジ拡大
  ├─ nextjs-fullstack-coder 新規作成
  ├─ go-backend-coder 新規作成
  ├─ composite スキルフォーマット + プリセットパイプライン 3 種
  ├─ performance-profiler 新規作成
  └─ incident-responder 新規作成

Phase 4 — 品質・運用の底上げ
  ├─ メトリクス集計・可視化基盤
  ├─ スクリプト化の拡大（security-reviewer, code-reviewer, api-designer）
  ├─ 長大 SKILL.md の references/ 分割（patent 系, ui-designer）
  ├─ バージョニングポリシーの統一（semver ガイドライン策定）
  ├─ debug-mode ⟷ systematic-debugging の連携フロー追加
  └─ refactoring-guide / infra-as-code 新規作成
```
