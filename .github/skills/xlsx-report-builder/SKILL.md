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

### T1: テンプレートの事実を読む

```bash
uv run python scripts/xlsx_builder.py inspect path/to/template.xlsx   # 判断用の事実（--json も可）
uv run python scripts/xlsx_builder.py analyze path/to/template.xlsx -o def.yaml   # 定義の下書き（自動検出）
```

`inspect` は、行ごとの値・数式・書式の種類（同じ見た目は同じ番号）・結合・条件付き書式と、**仮の値の疑い**（`サンプル`・`〇〇`・`yyyy` など）・**注記の疑い**（`※`）、同じ書式が続く範囲を出す。`analyze` は、そこから表を機械的に拾った下書き。**下書きは確定ではない**。判断は、`inspect` の事実を読んだこちら（LLM）が行う。

### T2: 範囲ごとに、役割を決める

テンプレートの各範囲に、次の 4 つのどれかを割り当てる。機械的な検出に任せず、**値の意味・書式・並び**から決める。

| 役割 | 意味 | 定義での書き方 | 向く範囲 |
|------|------|----------------|----------|
| 残す（keep） | テンプレートのまま出す | 何も書かない（既定） | タイトル・ラベル・合計行・固定の注記・ロゴ |
| 流し込む（fill） | データの値で置き換える | `cells`（固定セル）、`columns.<列>.key` | 宛名・日付・明細の値 |
| 繰り返す（repeat） | サンプル行を、データの件数だけ複製する | `tables`（`pattern`・`block_rows`）、数式は `formula: true` | 明細の行、1 件が複数行のカード |
| 無視する（ignore） | 出力に残さない | `clear`（値だけ空にして書式は残す）、`drop_rows`（行ごと取り除く） | 記入例・仮の値・ダミー行・使わない注意書き |

判断の目安:
- 同じ書式が並び、値が連番や例に見える行 → **繰り返す**。縞模様なら `pattern` に周期の行を並べる。1 件が 2 行以上（明細 + 備考など）なら `block_rows` を使う
- 仮の値の疑い（`サンプル株式会社`・`2020-01-01`）のうち、データで置き換える欄 → **流し込む**。置き換えない欄 → **無視する**（`clear`）
- 記入例の行や、利用者向けの書き方の注意（`※ 記入例`）→ **無視する**。印刷物として残すべき注記（`※ 振込手数料はご負担ください`）→ **残す**
- 合計・小計・税などの行 → **残す**。`SUM` の範囲は、表の行数に合わせて自動で伸びる
- 定数の列（`円` など）→ `keep: true`。サンプルの値を持ち越したくない列 → `clear: true`
- 迷う範囲は、決めずに**ユーザーに聞く**。聞くときは、範囲・候補の役割・理由を 1 行ずつ示す

決めたら、判断の一覧（範囲 | 役割 | 理由）を**ユーザーに見せて確認してもらう**。確認前に定義を確定しない。確定した定義には、判断の記録として `decisions`（範囲・役割・理由の一覧）を残してよい（render は読まない）。

### T3: データを用意して再構成する

データは、表ごとのキーにオブジェクトの配列を持つ JSON にする（[assets/template.data.example.json](assets/template.data.example.json)）。数値を捏造しない。

```bash
uv run python scripts/xlsx_builder.py render --def def.json --data data.json -o out.xlsx
```

テンプレートは定義の `template` から読む。別のパスなら `--template` で上書きする。定義ファイルとデータは JSON でも YAML（`.yaml` / `.yml`）でもよい。`analyze -o def.yaml` のように出力の拡張子で形式を選べる。

### T3b: この文書専用の単体スクリプトにする（任意）

同じ帳票を繰り返し作るなら、確定した定義を埋め込んだ、スキル不要の 1 ファイルを書き出せる。テンプレートの .xlsx は、既定では別ファイルのまま置く（スクリプトからの相対パスで読む）。

```bash
uv run python scripts/xlsx_builder.py export --def def.yaml -o render_invoice.py
uv run render_invoice.py --data data.yaml -o out.xlsx   # PEP 723 で依存（lxml・openpyxl・pyyaml）を自動で入れる
```

- 書き出した `.py` は、スキルのディレクトリが無くても動く。テンプレートを同じ場所に置いておく
- 配布を 1 ファイルにしたいときだけ `--embed` を付ける（テンプレートを base64 で埋め込む）
- `--example-data` でデータの雛形、`--extract-def` で埋め込みの定義を取り出せる。`--help` にデータの形が出る

#### 書き出したスクリプトの改修

スクリプトを手で書き換えない。定義を直して、書き出し直す。

```bash
python render_invoice.py --extract-def def.yaml            # 1. 埋め込みの定義を取り出す
#   def.yaml を直す（列の追加・キーの変更・pattern の見直しなど）
uv run python scripts/xlsx_builder.py export --from-script render_invoice.py --def def.yaml -o render_invoice.py   # 2. 書き出し直す
```

- 定義を直さずに `--from-script` だけを付けると、定義とテンプレートを引き継いで、最新のエンジンで書き出し直す（エンジンの更新）
- テンプレートが変わったときは `--template new.xlsx` を付ける。構造が変わったなら、`analyze` からやり直して定義を確定する
- 書き出し時に定義とテンプレートの整合を検査する。合わなければ書き出さない

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
