---
name: xlsx-report-builder
description: JSON スペックから Excel (.xlsx) 帳票・レポートを生成するスキル。「Excelを作って」「エクセルで帳票を作って」「xlsxを生成して」「集計表を作って」「売上レポートをExcelで」「スプレッドシートを出力して」「データをExcelにまとめて」などのリクエストで発動する。複数シート・見出し装飾・数値書式・合計行・条件付き書式・グラフ・フリーズペイン・オートフィルタに対応する。
metadata:
  version: 1.0.0
  tier: experimental
  category: document
  tags:
    - xlsx
    - excel
    - report
    - spreadsheet
    - openpyxl
    - json
---

# xlsx-report-builder

JSON スペックから Excel 帳票を生成する。値の集計・整形は **JSON スペックを組み立てる側（このスキルの呼び出し）** が担当し、ビルダーは見栄えと Excel 機能（書式・合計・グラフ）を付与する。

パスはこの SKILL.md からの相対パス。コマンド実行前にこのディレクトリに `cd` すること。

## 起動手順

スキル開始時に必ず実行する:

```bash
cd .github/skills/xlsx-report-builder
uv sync
```

## ワークフロー

### Step 1: データと出力イメージを確認する

1. 元データの所在（ユーザー提示・CSV・DB クエリ結果・コード生成等）を確認する
2. 帳票の体裁を確認する: シート構成・列・数値書式（通貨/パーセント/日付）・合計の要否・グラフの要否
3. 不明点（通貨単位・小数桁・期間の粒度など）があれば確認する。**勝手に数値を捏造しない**

### Step 2: JSON スペックを組み立てる

スペックの構造は [references/spec.md](references/spec.md) を参照。雛形は次で取得できる:

```bash
uv run python scripts/xlsx_builder.py example
```

ポイント:
- `columns[].key` と `rows[]` のキーを一致させる
- 金額は `number_format: "¥#,##0"`、率は `"0.0%"`、日付は `"yyyy-mm-dd"`（ISO 文字列は自動で日付型に変換）
- 合計が必要なら `total_row.sums` に対象列を指定（`SUM` 数式が入る）
- 見出し固定は `freeze: "A2"`、絞り込みは `auto_filter: true`

組み立てたスペックは作業ファイル（例 `spec.json`）に保存する。

### Step 3: 生成する

```bash
uv run python scripts/xlsx_builder.py build --spec spec.json
# または stdin から
cat spec.json | uv run python scripts/xlsx_builder.py build
```

`filename` に指定したパスへ `.xlsx` が出力される。

### Step 4: 検証して引き渡す

1. 生成された行数・合計・書式が意図通りか確認する（必要なら openpyxl で読み返す）
2. 出力ファイルのパスをユーザーに伝える

## できること / できないこと

| 対象 | 可否 |
|------|------|
| 複数シート・見出し装飾・数値書式 | ✅ |
| 合計行（SUM）・フリーズペイン・オートフィルタ | ✅ |
| 条件付き書式（カラースケール / データバー / しきい値） | ✅ |
| グラフ（棒 / 折れ線 / 円、単・複数系列） | ✅ |
| ピボットテーブル・マクロ(VBA)・複雑な相互参照数式 | ❌ 非対応（必要なら別途相談） |
| 既存 .xlsx の編集 | ❌ 本スキルは新規生成のみ |

## ガードレール

| 制限 | 内容 |
|------|------|
| データの事実性 | スペックに無い数値を生成しない。集計は呼び出し側で確定させる |
| 機密 | 元データのシークレット・個人情報の取り扱いに注意し、不要な列を含めない |
| スコープ | 帳票生成に集中する。データ取得・分析そのものは別スキル/別タスク |
