---
name: code-to-specs
description: 既存コードから仕様書を逆生成するスキル。「仕様書を作って」「スペックを生成して」「コードから仕様を起こして」「実装から仕様書を書いて」「リバースエンジニアリングして仕様書を作って」「既存システムの仕様をまとめて」などで発動する。レガシーコードや既存実装から、保守担当者・顧客・SME向けの信頼性の高い仕様書を生成する。
metadata:
  version: "1.0.0"
  tier: experimental
  category: documentation
  tags:
    - reverse-engineering
    - specification
    - documentation
    - code-analysis
---

# code-to-specs

既存のコードベースを解析して仕様書を逆生成するスキル。推測と確実性を明示し、トレーサビリティを確保した実務的な仕様書を生成する。

## 設計原則

- **正直さ優先**: 完璧さより、推測部分の明示を重視する。`[ASSUMED]`・`[ASK SME]` マーカーを積極的に使う
- **トレーサビリティ**: すべての記述にソースコード参照 `[REF: ファイル:行番号]` を付与する
- **段階的詳細化**: 各フェーズでレビューポイントを設ける
- **再開可能性**: `.specs-work/state.json` で進捗を管理し、中断・再開を可能にする

## エージェント互換性

このスキルは **Claude Code・GitHub Copilot・kiro-cli** で動作する。エージェント固有の機能（サブエージェント並列起動など）が使えない場合は、ステップを逐次実行する。

---

## 事前準備: 既存スキルの確認

このスキルの各フェーズでは、ワークスペースに存在する既存スキルを活用する。処理開始前に次の対応を把握しておく:

| フェーズ | 活用できるスキル（存在する場合） |
|---|---|
| Phase 1: 偵察 | `tech-harvester`（技術スタック調査）, `dependency-auditor`（依存関係分析） |
| Phase 2: 計画 | `domain-modeler`（ドメインモデル抽出）, `api-designer`（API仕様把握） |
| Phase 3: 調査 | `deep-research`（深堀り調査）, `agent-reviewer`（コードレビュー的視点） |
| Phase 4: 検証 | `agent-reviewer`（仕様書レビュー）, `self-checking`（整合性確認） |
| Phase 5: 精緻化 | `technical-writer`（文書品質向上） |
| Phase 6: 納品 | `technical-writer`（最終文書整形） |

各スキルが `.github/skills/` に存在するか確認し、存在する場合はそのスキルに処理を委譲または参照する。

---

## 6フェーズのプロセス

各フェーズ完了後に `state.json` を更新し、次フェーズへ進む前にユーザーの確認を挟む。

### Phase 0: セットアップとゴール定義

**目的**: 対象範囲・読者・粒度を確定する。

以下の5問をユーザーに確認する（選択肢を提示する）:

```
Q1. 仕様書の主な読者は誰ですか？
    a) 保守開発者  b) 顧客・ビジネスサイド  c) 監査・コンプライアンス  d) 新規参加エンジニア

Q2. 読者はこの仕様書で何をしますか？
    a) 変更・機能追加の参照  b) 承認・レビュー  c) 監査・検査  d) システム理解

Q3. 希望する粒度はどのレベルですか？
    a) 概要（構成・全体像のみ）  b) 中粒度（機能・フロー）  c) 詳細（内部ロジック・DB設計含む）

Q4. 重視する観点はどれですか？（複数可）
    a) 機能仕様  b) 業務ロジック  c) API・インターフェース  d) セキュリティ  e) データモデル

Q5. 既存のドキュメントや設計書はありますか？
    a) なし  b) README程度  c) 設計書・ERD等がある  d) 詳細な仕様書がある
```

回答を `.specs-work/goal.json` に保存する:

```json
{
  "reader": "保守開発者",
  "purpose": "変更・機能追加の参照",
  "granularity": "中粒度",
  "focus": ["機能仕様", "API・インターフェース"],
  "existing_docs": "なし",
  "target_path": ".",
  "started_at": "2025-01-01T00:00:00Z"
}
```

---

### Phase 1: 偵察とテンプレート選定

**目的**: コードベース全体像を把握し、仕様書テンプレートを決定する。

#### 1-1. 浅い偵察

```bash
# ファイルツリーを深さ3〜4で確認
find . -maxdepth 4 -not -path '*/.git/*' -not -path '*/node_modules/*' \
  -not -path '*/__pycache__/*' -not -path '*/vendor/*' | sort

# エントリポイント・設定ファイルを確認
ls -la
```

`tech-harvester` スキルがある場合は委譲する:
> 「tech-harvester スキルを使って、このプロジェクトの技術スタックを調査してください」

`dependency-auditor` スキルがある場合は委譲する:
> 「dependency-auditor スキルを使って、依存関係を分析してください」

#### 1-2. テンプレート選択

偵察結果からテンプレートを選ぶ（詳細は [references/templates.md](references/templates.md) 参照）:

| テンプレート | 選択条件 |
|---|---|
| **Webアプリケーション仕様書** | フロントエンド+バックエンド構成、ルーティングがある |
| **APIサービス仕様書** | REST/GraphQL/gRPC エンドポイント中心 |
| **バッチ処理システム仕様書** | ジョブ・スケジューラ・ETL構成 |
| **ライブラリ/SDK仕様書** | npm/pip等でパッケージ配布される |
| **モノリシックシステム仕様書** | 大規模レガシー、複合構成 |

選択したテンプレートと偵察で見えた大局的な疑問を `.specs-work/recon-report.md` に記録する。

---

### Phase 2: 計画とWBS分割

**目的**: 仕様書の骨組みを確定し、調査タスクを設計する。

#### 2-1. コードインベントリの抽出

言語に応じてインベントリ単位を抽出する（詳細は [references/inventory-guide.md](references/inventory-guide.md) 参照）:

| 言語 | 抽出単位 |
|---|---|
| Python | モジュール、クラス、関数、エンドポイント |
| TypeScript/JavaScript | モジュール、クラス、関数、ルート定義 |
| Java/Kotlin | クラス、メソッド、エンティティ |
| Go | パッケージ、構造体、関数 |
| PHP | クラス、トレイト、関数、ルート |

`domain-modeler` スキルがある場合は委譲する:
> 「domain-modeler スキルの逆引きモードで、このコードからドメインモデルを抽出してください」

`api-designer` スキルがある場合は委譲する:
> 「api-designer スキルを使って、エンドポイント一覧を整理してください」

抽出結果を `.specs-work/inventory.json` に保存する。

#### 2-2. 章構成の決定（WBS）

ファイル名規約: `NN-slug.md`（`NN`: 2桁ゼロ埋め章番号、`slug`: ASCII小文字/数字/ハイフン）

必須章:
- `00-metadata.md`: 生成メタデータ（Phase 6で充填）
- `99-unresolved.md`: 未確定事項（Phase 6で充填）
- `traceability.md`: コード参照対応表

WBSを `.specs-work/wbs.json` に保存する。

---

### Phase 3: 並列調査（章ドラフト生成）

**目的**: WBSに基づいて各章のドラフトを生成する。

**サブエージェント並列起動が使える環境**（Claude Code）では各章を同時実行し、**使えない環境**（Copilot/Kiro）では章ごとに逐次実行する。

#### 不確実性マーカーの使い方

すべての記述に以下のマーカーを付ける:

| マーカー | 意味 | 使用場面 |
|---|---|---|
| `[CONFIDENCE: HIGH]` | コードから明確に読み取れる | 関数定義、明示的な設定値 |
| `[CONFIDENCE: MED]` | 文脈から推測できる | コメントや命名から推定した挙動 |
| `[CONFIDENCE: LOW]` | 推測に依存する | コードの意図が不明瞭 |
| `[ASSUMED: 内容]` | このように推測した | 根拠が薄い判断 |
| `[ASK SME]` | 専門家に確認が必要 | 業務ルール、歴史的経緯 |
| `[BLOCKED]` | critical疑問で記述不可 | 章全体が書けない場合 |

すべての記述にコード参照を付与する: `[REF: src/app.py:42-58]`

#### 疑問のQuestion Bank登録

調査中に生まれた疑問を `.specs-work/questions.json` に追加する:

```json
{
  "id": "Q-001",
  "phase": "investigation",
  "category": "business_rule",
  "severity": "critical",
  "status": "open",
  "question": "ユーザー認証のタイムアウト値（30分）はどこで決定されたか？",
  "source_ref": "src/auth/middleware.py:87"
}
```

**7つの疑問カテゴリ**:
1. `business_rule`: 業務ルール・条件分岐の意図
2. `architecture_decision`: 設計判断の経緯
3. `data_model_intent`: データモデルの意図・制約
4. `external_integration`: 外部システム連携の仕様
5. `naming_history`: 命名・歴史的経緯
6. `operational_requirement`: 運用・監視要件
7. `security_compliance`: セキュリティ・コンプライアンス

`deep-research` スキルがある場合は特定の深堀り調査に委譲する:
> 「deep-research スキルで、このプロジェクトで使われている [フレームワーク名] の [機能名] の動作仕様を調査してください」

ドラフトを `.specs-work/drafts/` 配下に保存する。

---

### Phase 4: 検証

**目的**: 漏れと矛盾を検出する。

#### 4-1. インベントリカバレッジ検証

`inventory.json` のすべての項目がいずれかの章で言及されているか確認する:

```bash
python .github/skills/code-to-specs/scripts/coverage_check.py
```

#### 4-2. 章間整合性検証

`agent-reviewer` スキルがある場合は仕様書レビューに委譲する:
> 「agent-reviewer スキルで、.specs-work/drafts/ 以下の仕様書ドラフトをレビューしてください。perspectiveは 'document' を使用してください」

`self-checking` スキルがある場合は整合性確認に活用する。

手動確認チェックリスト:
- [ ] すべての章でコード参照が付いているか
- [ ] `[BLOCKED]` マーカーの章は疑問がQuestion Bankに登録されているか
- [ ] 章間で矛盾する記述がないか（例: 認証方式が複数の章で異なる）

検証結果を `.specs-work/coverage-report.md` に保存する。

---

### Phase 5: 対話による精緻化

**目的**: 不確実性と未解決疑問を解消する。

#### 5-1. 疑問の規模提示

```
未解決疑問: 計XX件
  critical: X件（解決しないと章が書けない）
  important: X件（推測で進められるが確認推奨）
  nice-to-have: X件（なくても仕様書は成立する）
```

#### 5-2. 優先度別クラスタリング

疑問をカテゴリ・severity別にグループ化してユーザーに提示する。

#### 5-3. 個別疑問の解消

各疑問に対してユーザーに選択肢を提示する:

```
Q-001: ユーザー認証のタイムアウト値（30分）はどこで決定されたか？
[REF: src/auth/middleware.py:87]

対処方法を選んでください:
  a) 推測で進めてOK（推測内容: セキュリティ要件から標準的な値を採用）
  b) 正解を入力する → [入力してください]
  c) SME確認が必要（後で確認する）
  d) 永遠に不明（"abandoned"としてマーク）
```

回答に基づいてドラフトを更新し、`questions.json` のステータスを更新する。

`technical-writer` スキルがある場合は文書品質向上に活用する:
> 「technical-writer スキルで、.specs-work/drafts/ の文書を日本語として自然に整えてください」

---

### Phase 6: 納品

**目的**: 最終仕様書一式を生成する。

#### 6-1. 最終成果物の生成

```bash
mkdir -p .specs-work/final
cp .specs-work/drafts/*.md .specs-work/final/
```

#### 6-2. 特別章の充填

**`00-metadata.md`**:

```markdown
# 仕様書メタデータ

| 項目 | 値 |
|---|---|
| 生成日時 | {ISO8601} |
| 対象コミット | {git rev-parse HEAD} |
| 生成スキル | code-to-specs v1.0.0 |
| 読者 | {goal.json から} |
| 粒度 | {goal.json から} |
| 重視観点 | {goal.json から} |

## 信頼性について

この仕様書はコードから逆生成されています。
`[CONFIDENCE: HIGH]` — コードから明確に読み取れた記述
`[CONFIDENCE: MED]` — 文脈から推測した記述
`[CONFIDENCE: LOW]` — 推測に依存する記述（要確認）
`[ASSUMED]` — 明示的な推測
`[ASK SME]` — 専門家確認推奨
```

**`99-unresolved.md`**: `abandoned` ステータスの疑問を「未確定事項」として記載する。

**`traceability.md`**: 仕様書セクション ↔ コード行番号の対応表を生成する。

#### 6-3. README の生成

`technical-writer` スキルがある場合は委譲する:
> 「technical-writer スキルで、.specs-work/final/ の成果物一覧と読み方ガイドをREADMEとして作成してください」

---

## 作業ディレクトリ構造

```
.specs-work/
├── state.json          # 進捗管理（currentPhase, completedPhases）
├── goal.json           # ゴール定義（Phase 0）
├── recon-report.md     # 偵察レポート（Phase 1）
├── inventory.json      # 抽出インベントリ（Phase 2）
├── wbs.json            # 作業分解（Phase 2）
├── questions.json      # Question Bank（Phase 3〜5）
├── coverage-report.md  # 検証レポート（Phase 4）
├── drafts/             # 章ドラフト（Phase 3）
│   ├── 01-overview.md
│   └── ...
└── final/              # 最終成果物（Phase 6）
    ├── 00-metadata.md
    ├── 01-overview.md
    ├── 99-unresolved.md
    ├── traceability.md
    └── README.md
```

## 再開プロトコル

「仕様書生成を再開して」と言われた場合:

1. `.specs-work/state.json` を読む
2. `currentPhase` から再開する
3. 「Phase X から再開します。前回の進捗: [概要]」とユーザーに伝える
