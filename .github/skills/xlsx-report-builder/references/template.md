# テンプレート流し込み（analyze / render）

既存の .xlsx テンプレートに、書式・不変部分をそのまま保ってデータを流し込む。
テンプレートの罫線・フォント・セル色・列幅・結合セル・図・グラフ・他シートは、元のまま残る。

## 仕組み

xlsx（zip）の中のシート XML のうち、**可変の表の行だけ**を書き換える。openpyxl で開いて保存し直さない。

- 表の「サンプル行」は、ひな形として複製される。書式（スタイル）・行の高さ・数式・結合セルごと複製する
- 表の下にある行（合計行・注記・別の表）は、増減した行数だけずれる
- ずれに合わせて、次の参照も更新する
  - 数式（`=SUM(E8:E9)` → `=SUM(E8:E12)`）、他シートからの参照
  - 条件付き書式・入力規則・結合セル・オートフィルタ・定義名・Excel テーブル・グラフの範囲・図の位置
- 数式の結果は保存しない。開いたときに Excel が全再計算する（`fullCalcOnLoad`）

## 入出力の形式

定義ファイルとデータは、JSON と YAML のどちらでも渡せる（拡張子 `.json` / `.yaml` / `.yml` で判別。`--data -` は標準入力で、JSON → YAML の順に試す）。YAML の日付（`2026-10-05`）は日付型で読まれるが、日付書式のセルにはそのまま日付として入る。

## 定義ファイル（JSON / YAML）

```json
{
  "version": 1,
  "template": "請求書.xlsx",
  "sheets": [{
    "name": "請求書",
    "cells": {"B3": "customer", "B4": "date"},
    "tables": [{
      "id": "items",
      "header_row": 7,
      "first_row": 8,
      "sample_rows": 2,
      "pattern": [8, 9],
      "key": "items",
      "columns": {
        "A": {"key": "$index"},
        "B": {"key": "name"},
        "C": {"key": "qty"},
        "D": {"key": "price"},
        "E": {"formula": true}
      }
    }]
  }]
}
```

| 項目 | 意味 |
|------|------|
| `cells` | 表の外の固定セル。`セル: データのキー`。キーは `a.b.0` のようにドットでたどれる |
| `tables[].first_row` / `sample_rows` | テンプレートのサンプル行の範囲（先頭行と行数） |
| `tables[].pattern` | 複製元にするサンプル行。複数行なら順に繰り返す（縞模様は 2 行） |
| `tables[].key` | データ側の配列のキー（オブジェクトの配列） |
| `columns.<列>.key` | その列に入れる、行オブジェクトのキー。`$index` は 1 始まりの連番 |
| `columns.<列>.formula` | `true` ならサンプル行の数式を、行に合わせてずらして複製する |
| `columns.<列>.keep` | `true` ならサンプル行の値をそのまま残す（定数の列） |
| `columns.<列>.clear` | `true` なら値を空にする |
| `sheets[].clear` | **無視**する値。`["A2", "B5:D7"]`。書式は残して値だけ空にする（表の外のセル） |
| `sheets[].drop_rows` | **無視**する行。`[11, "20:22"]`。出力から行ごと取り除き、下の行を詰める |
| `tables[].block_rows` | 1 件が何行か（既定 1）。`sample_rows` は件数 × `block_rows`。`pattern` はブロックの先頭行 |
| `tables[].block` | `block_rows` が 2 以上のとき、行ごとの `columns` のリスト（`block_rows` 個） |
| `decisions` | 判断の記録（範囲・役割・理由）。render は読まない |
| `_` で始まる項目、`needs_confirm`、`header` | analyze が付ける確認用の情報。render は読まない |

- 定義に無い列は、サンプル行のまま複製される（不変）
- データが 0 行でも、空欄の 1 行を残す（合計の範囲が壊れないように）
- ISO 形式の日付文字列（`2026-10-05`）は、日付の書式のセルなら日付として入る。その他の文字列はそのまま文字として入る
- 複数の表・複数のシートを 1 つの定義に書ける。表のサンプル行は重ねない

## 役割（残す・流し込む・繰り返す・無視する）

| 役割 | 書き方 |
|------|--------|
| 残す | 何も書かない（既定）。定義に無い範囲・列は、テンプレートのまま出る |
| 流し込む | `cells`（固定セル）、`columns.<列>.key` |
| 繰り返す | `tables`。1 行ずつなら `pattern`、1 件が複数行なら `block_rows` + `block` |
| 無視する | `clear`（値だけ空に）、`drop_rows`（行ごと削除） |

1 件が 2 行のカード形式の例（`block_rows: 2`）:

```yaml
tables:
  - id: cards
    header_row: 4
    first_row: 5
    sample_rows: 4        # 2 件 × 2 行
    block_rows: 2
    pattern: [5, 7]       # 縞模様はレコード単位。ブロックの先頭行を並べる
    key: cards
    block:                # 行ごとの列指定
      - {A: {key: name}, B: {key: amount}, C: {key: due}}
      - {A: {key: note}}
```

どの範囲にどの役割を当てるかは、`inspect` の出力（値・書式の種類・仮の値の疑い）を読んで決める。

## 数式の複製ルール（Excel の「下方向へコピー」と同じ）

- 相対参照はコピー先の行へずれる（`=C8*D8` → `=C9*D9`）。直前の行を指す `=E7+C8` も、行ごとにずれる
- `$` 付きの絶対参照は動かさない（税率セル `$B$5` など）。表の下にある行への相対参照は、表の外と見なして固定する
- 表の外の数式は、表の最終サンプル行を指す参照（範囲の末尾）が、出力の最終行に伸びる
- 表の外の行を相対参照している明細の数式は、analyze が「確認」に出す

## 制限

- ひな形に使わないサンプル行を跨ぐ結合セル（複数行にまたがるもの）は取り除かれる（警告が出る）
- コメント（メモ）は、行がずれても位置が追従しない場合がある
- `analyze` の見出し判定は、見出しが太字か塗りつぶしの行を想定した下書き。判断は `inspect` の事実を読んで行い、外れたときは定義ファイルを直す
- グラフの計算キャッシュは更新しない（Excel が開いたときに再描画する）

## 専用スクリプトの書き出し（export）

```bash
uv run python scripts/xlsx_builder.py export --def def.yaml -o render_invoice.py
```

- 出力は、エンジンと定義を 1 つにまとめた Python ファイル。スキルのパスに依存しない
- **テンプレートは既定では別ファイル**のまま、スクリプトからの相対パスで読む。`--embed` を付けたときだけ base64 で埋め込む
- 先頭に PEP 723 の依存宣言があり、`uv run render_invoice.py --data data.yaml -o out.xlsx` でそのまま動く。`python` で動かすときは lxml・openpyxl・pyyaml を入れておく
- 引数: `--data`（json/yaml/`-`）、`-o`、`--template`（別の .xlsx に差し替える。構造が同じものに限る）、`--example-data`、`--extract-def PATH`、`--extract-template PATH`（埋め込み時のみ）
- 書き出し時に、テンプレートと定義の整合（シート名・サンプル行・pattern・列）を検査する

### 改修

書き出したスクリプトは、スキルで直せる。スクリプトを手で書き換えず、定義を直して書き出し直す。

1. `python render_invoice.py --extract-def def.yaml`（スキルからは `export` が `--from-script` で直接読むので、この手順は手で直したいときだけ）
2. `def.yaml` を直す
3. `export --from-script render_invoice.py --def def.yaml -o render_invoice.py`

| やりたいこと | コマンド |
|---|---|
| 定義を直す | `export --from-script old.py --def def.yaml -o new.py` |
| エンジンを最新にする | `export --from-script old.py -o new.py`（定義・テンプレートは引き継ぐ） |
| テンプレートを差し替える | `export --from-script old.py --template new.xlsx -o new.py` |

- `--from-script` は、スクリプトを実行せず、埋め込みの定数だけを読む（`ast`）。export が書き出したものでなければ、エラーにする
- 埋め込み版のスクリプトからは、埋め込みのテンプレートも引き継ぐ。相対参照版は、新しい出力先からの相対パスに直す
- 表の構造が変わった（列や表の追加）ときは、`analyze` から定義を作り直す
