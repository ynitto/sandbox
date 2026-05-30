# xlsx-report-builder スペック仕様

`xlsx_builder.py build` が受け取る JSON スペックの構造。

## トップレベル

```json
{
  "filename": "report.xlsx",
  "properties": { "title": "...", "creator": "..." },
  "sheets": [ /* シート定義（1つ以上必須） */ ]
}
```

| キー | 必須 | 説明 |
|------|------|------|
| `filename` | 任意 | 出力ファイル名（既定 `report.xlsx`） |
| `properties` | 任意 | ブックのメタデータ（`title` / `creator`） |
| `sheets` | **必須** | シート定義の配列（最低1つ） |

## シート定義

```json
{
  "name": "売上明細",
  "freeze": "A2",
  "auto_filter": true,
  "header_style": { "bold": true, "bg": "305496", "font_color": "FFFFFF", "align": "center" },
  "columns": [ /* 列定義 */ ],
  "rows": [ /* 行データ（オブジェクトの配列、キーは列の key） */ ],
  "total_row": { "label_col": "product", "label": "合計", "sums": ["qty", "amount"] },
  "conditional": [ /* 条件付き書式 */ ],
  "charts": [ /* グラフ */ ]
}
```

| キー | 必須 | 説明 |
|------|------|------|
| `name` | **必須** | シート名（31文字まで） |
| `columns` | **必須** | 列定義の配列。表示順に並べる |
| `rows` | 任意 | 各行はオブジェクト。`columns[].key` で値を参照 |
| `freeze` | 任意 | フリーズペインのセル（例 `"A2"` で見出し固定） |
| `auto_filter` | 任意 | `true` で見出し行にオートフィルタを付与 |
| `header_style` | 任意 | 見出しの装飾（下表） |
| `total_row` | 任意 | 合計行。`sums` の各列に `SUM` 数式を入れる |
| `conditional` | 任意 | 条件付き書式の配列 |
| `charts` | 任意 | グラフの配列 |

## 列定義 (`columns[]`)

| キー | 必須 | 説明 |
|------|------|------|
| `key` | **必須** | `rows` のオブジェクトと対応するキー |
| `header` | 任意 | 見出し文字列（既定は `key`） |
| `width` | 任意 | 列幅 |
| `number_format` | 任意 | 表示形式（`#,##0` / `¥#,##0` / `0.0%` / `yyyy-mm-dd` 等） |
| `align` | 任意 | セル水平揃え（`left` / `center` / `right`） |

**日付の自動変換**: `number_format` が日付系（`y` または `d` を含む）で、値が ISO 文字列（`2026-05-01`）の場合、自動で日付型セルに変換される。

## header_style

| キー | 既定 | 説明 |
|------|------|------|
| `bold` | `true` | 太字 |
| `bg` | `4472C4` | 背景色（RGB 16進、`#` なし） |
| `font_color` | `FFFFFF` | 文字色 |
| `align` | `center` | 水平揃え |

## total_row

| キー | 説明 |
|------|------|
| `label_col` | ラベルを置く列の `key`（既定は先頭列） |
| `label` | ラベル文字列（既定 `合計`） |
| `sums` | `SUM` 数式を入れる列 `key` の配列 |

## conditional[]（条件付き書式）

| `type` | 追加キー | 説明 |
|--------|---------|------|
| `color_scale` | `min_color` / `mid_color` / `max_color` | 3色カラースケール |
| `data_bar` | `color` | データバー |
| `greater_than` | `value`, `fill` | 値が `value` 超のセルを `fill` 色で塗る |

共通: `range_col` に対象列の `key` を指定（データ範囲に適用）。

## charts[]（グラフ）

| キー | 必須 | 説明 |
|------|------|------|
| `type` | 任意 | `bar` / `line` / `pie`（既定 `bar`） |
| `title` | 任意 | グラフタイトル |
| `categories_col` | **必須** | 軸（カテゴリ）にする列の `key` |
| `values_col` | ※ | 値にする列の `key`（単系列） |
| `values_cols` | ※ | 複数系列にする列 `key` の配列 |
| `anchor` | 任意 | 配置セル（既定 `H2`） |

※ `values_col` か `values_cols` のいずれかを指定する。
