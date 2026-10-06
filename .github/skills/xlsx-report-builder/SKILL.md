---
name: xlsx-report-builder
description: JSON スペックから Excel (.xlsx) 帳票・レポートを新規生成するスキル。既存の .xlsx テンプレートに、罫線・フォント・セル色などの書式を保ったままデータを流し込む（行数が可変の表・数式の複製・合計行のずれに対応）こともできる。「Excelを作って」「エクセルで帳票を作って」「xlsxを生成して」「集計表を作って」「売上レポートをExcelで」「スプレッドシートを出力して」「データをExcelにまとめて」「Excelのテンプレートにデータを流し込んで」「テンプレートの書式を保ったままxlsxを作って」などのリクエストで発動する。複数シート・見出し装飾・数値書式・合計行・条件付き書式・グラフ・フリーズペイン・オートフィルタに対応する。
metadata:
  version: 1.1.0
  tier: experimental
  category: document
  tags:
    - xlsx
    - excel
    - report
    - spreadsheet
    - openpyxl
    - json
    - template
---

# xlsx-report-builder

JSON スペックから Excel 帳票を生成する。また、既存の .xlsx テンプレートへ書式を保ったままデータを流し込める（[下記](#テンプレートへの流し込み)）。

**使い分け**: テンプレート（体裁の決まった .xlsx）がある → 「テンプレートへの流し込み」。無い → 新規生成（Step 1〜4）。
値の集計・整形は **JSON スペックを組み立てる側（このスキルの呼び出し）** が担当し、ビルダーは見栄えと Excel 機能（書式・合計・グラフ）を付与する。

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

## テンプレートへの流し込み

テンプレートはサンプル値が入っていることが多く、表の行数も可変になる。そこで、構造を解析して定義ファイルを作る（確認あり）→ 定義とデータから再構成する、の 2 段で行う。不変部分（書式・列幅・図・グラフ・他シート）は元のままコピーされる。詳細は [references/template.md](references/template.md)。

### T1: 解析して定義ファイルの下書きを作る

```bash
uv run python scripts/xlsx_builder.py analyze path/to/template.xlsx -o def.json
```

標準出力に、見つけた表（見出し行・サンプル行・繰り返し元・列ごとの型/書式/数式）と、表の外の値の候補、注意点が出る。

### T2: ユーザーに確認して定義を確定する

要約の **`?` の項目と、データのキー（key）** をユーザーに見せて確認する。確認することは次のとおり。

1. 表の範囲（見出し行・サンプル行）と繰り返し元の行（縞模様なら 2 行）が合っているか
2. 各列に入れるデータのキー。連番の列は `$index`、数式の列は `"formula": true`、定数の列は `"keep": true`
3. 表の外のどのセルに何を流し込むか（`cells`）。`_candidates` を見せて選んでもらう

確定したら `def.json` を直す。**ユーザーの確認なしに、解析結果をそのまま確定しない**。

### T3: データを用意して再構成する

データは、表ごとのキーにオブジェクトの配列を持つ JSON にする（[assets/template.data.example.json](assets/template.data.example.json)）。数値を捏造しない。

```bash
uv run python scripts/xlsx_builder.py render --def def.json --data data.json -o out.xlsx
```

テンプレートは定義の `template` から読む。別のパスなら `--template` で上書きする。

### T4: 検証する

1. 出力を openpyxl で読み返し、行数・値・数式（`=SUM(E8:E12)` など）が意図どおりか確認する
2. 書式（罫線・色・フォント）がテンプレートと同じか、サンプルの値が残っていないかを確認する
3. 数式の結果は、Excel で開いたときに再計算される。値を確かめたいときは LibreOffice で CSV に変換する

テストの実行: `uv run python -m unittest discover -s tests -v`

## できること / できないこと

| 対象 | 可否 |
|------|------|
| 複数シート・見出し装飾・数値書式 | ✅ |
| 合計行（SUM）・フリーズペイン・オートフィルタ | ✅ |
| 条件付き書式（カラースケール / データバー / しきい値） | ✅ |
| グラフ（棒 / 折れ線 / 円、単・複数系列） | ✅ |
| ピボットテーブル・マクロ(VBA)・複雑な相互参照数式 | ❌ 非対応（必要なら別途相談） |
| 既存 .xlsx テンプレートへの流し込み（書式・不変部分を保持） | ✅ `analyze` / `render` |
| 既存 .xlsx の任意の編集 | ❌ 流し込み以外は対象外 |

## ガードレール

| 制限 | 内容 |
|------|------|
| データの事実性 | スペックに無い数値を生成しない。集計は呼び出し側で確定させる |
| 機密 | 元データのシークレット・個人情報の取り扱いに注意し、不要な列を含めない |
| スコープ | 帳票生成に集中する。データ取得・分析そのものは別スキル/別タスク |
