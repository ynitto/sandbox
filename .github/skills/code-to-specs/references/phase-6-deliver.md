# Phase 6: 納品

**目的**: ドラフト章と特別章を統合し、最終仕様書一式を `final/` に生成する。

## 目次

- [6-1. ドラフトを final/ にコピー](#6-1-ドラフトを-final-にコピー)
- [6-2. 特別章の充填](#6-2-特別章の充填)
- [6-3. README.md の生成](#6-3-readmemd-の生成)
- [6-4. 最終報告](#6-4-最終報告)
- [完了条件](#完了条件)

---

## 手順

### 6-1. ドラフトを final/ にコピー

```bash
mkdir -p .specs-work/final
cp .specs-work/drafts/*.md .specs-work/final/
```

### 6-2. 特別章の充填

#### `00-metadata.md`

```markdown
# 仕様書メタデータ

| 項目 | 値 |
|---|---|
| 生成日時 | {ISO8601形式の現在日時} |
| 対象コミット | {git rev-parse HEAD の出力} |
| 生成スキル | code-to-specs v1.1.0 |
| 読者 | {goal.json の reader} |
| 粒度 | {goal.json の granularity} |
| 重視観点 | {goal.json の focus} |
| テンプレート | {recon-report.md の選択テンプレート} |

## 章一覧

| ファイル | タイトル | 信頼性 |
|---|---|---|
| 01-overview.md | サービス概要 | MED |
| ... | ... | ... |

## 信頼性マーカーの凡例

| マーカー | 意味 |
|---|---|
| `[CONFIDENCE: HIGH]` | コードから明確に読み取れた記述 |
| `[CONFIDENCE: MED]` | 文脈・命名から推測した記述 |
| `[CONFIDENCE: LOW]` | 推測に依存する記述（要確認推奨） |
| `[ASSUMED: 内容]` | 明示的な推測（根拠付き） |
| `[ASK SME]` | 専門家・ドメイン担当者への確認推奨 |
| `[REF: ファイル:行]` | ソースコード参照 |

## 利用上の注意

この仕様書はコードから逆生成されています。記述の正確性はソースコードの可読性に依存します。
`[CONFIDENCE: LOW]` や `[ASK SME]` のある記述は、ドメイン知識を持つ担当者によるレビューを推奨します。
```

#### `99-unresolved.md`

`status: abandoned` の疑問を「未確定事項」として記載する:

```markdown
# 未確定事項

この章には、調査・対話を経ても確定できなかった事項を記載します。
将来的に確認できた場合は、該当章を更新してこの章から削除してください。

## 業務ルール

### UR-001: キャンセル期限の根拠
- **疑問**: 注文キャンセル期限（24時間）の法的・業務的根拠
- **コード参照**: `src/orders/cancel_policy.py:42`
- **現時点の推測**: 業界慣行に基づく判断と推測
- **確定に必要なもの**: 法務担当者または発注元との確認
- **関連章**: `05-order-flow.md`

## アーキテクチャ判断

...
```

#### `traceability.md`

仕様書セクション ↔ ソースコードの対応表を生成する:

```markdown
# トレーサビリティ表

| 仕様書セクション | ソースコード参照 | 信頼性 |
|---|---|---|
| 01-overview.md § サービス概要 | README.md:1-10 | HIGH |
| 03-authentication.md § JWT検証 | src/auth/jwt.py:15-48 | HIGH |
| 04-endpoints.md § POST /orders | src/routers/orders.py:52-89 | MED |
| ... | ... | ... |
```

### 6-3. README.md の生成

`final/README.md` に以下を記載する:

```markdown
# 仕様書の読み方

生成日: {日時}  対象: {リポジトリ名}

## 章構成

| ファイル | タイトル | 内容 |
|---|---|---|
| 00-metadata.md | メタデータ | 生成情報・信頼性の凡例 |
| 01-overview.md | サービス概要 | システムの目的・全体像 |
| ... | ... | ... |
| 99-unresolved.md | 未確定事項 | 確認が必要な事項の一覧 |
| traceability.md | トレーサビリティ表 | 仕様書↔コードの対応 |

## 信頼性について

[CONFIDENCE: HIGH] の記述はコードから明確に確認済みです。
[CONFIDENCE: MED/LOW] や [ASSUMED] の記述は推測を含みます。
[ASK SME] マークの箇所はドメイン担当者への確認を推奨します。

## 未確定事項について

99-unresolved.md に記載された事項は、将来確認できた時点で該当章を更新してください。
```

### 6-4. 最終報告

ユーザーに成果物を報告する:

```
=== Phase 6 完了: 仕様書を納品しました ===

成果物: .specs-work/final/
  ├── 00-metadata.md    （メタデータ・凡例）
  ├── 01-overview.md
  ├── ...（全XX章）
  ├── 99-unresolved.md  （未確定事項: Y件）
  ├── traceability.md   （コード参照対応表）
  └── README.md         （読み方ガイド）

統計:
  - 総章数: XX章
  - [CONFIDENCE: HIGH]: XX% の記述
  - 未確定事項: Y件（99-unresolved.md 参照）
  - ソースコード参照: 合計 ZZ件
```

---

## 完了条件

- [ ] `final/` 配下に全章ファイルが存在する
- [ ] `00-metadata.md`, `99-unresolved.md`, `traceability.md`, `README.md` が生成されている
- [ ] `state.json` の `currentPhase` を `complete` に更新する
