---
name: spec-value-finder
description: "元仕様書(Excel/Word/PowerPoint/PDF/Markdown/txt)から記入すべき値を探すスキル。人が用意した仕様書をファイル名の部分一致で特定し、マッピング情報をもとに値を探して出典・確信度付きで記入シートに落とし込む。「仕様書に書く値を探して」「元仕様書から値を落とし込んで」「設定値を仕様書から拾って」「記入シートを埋めて」などで発動。サーバ・GPU不要。"
metadata:
  version: "1.1.0"
  tier: experimental
  category: documentation
  tags:
    - excel
    - word
    - powerpoint
    - pdf
    - specification
    - document-extraction
    - value-lookup
    - portable
---

# spec-value-finder

人が用意した元仕様書（Excel / Word / PowerPoint / PDF / Markdown / txt）から「記入すべき値」を探し、出典・確信度・要確認フラグ付きで記入シートに落とし込むスキル。

> **設計思想**: Neo4j も Table Transformer も使わない。依存は pip 一発で入る純Python系ライブラリ（openpyxl / python-docx / python-pptx / pypdfium2 / PyYAML、数MB）だけで、サーバ起動も GPU も不要。どの環境でも `init` 一発で動く可搬性を最優先する。

## 役割分担

| 担当 | 内容 |
|------|------|
| **スクリプト**（決定的な機械作業） | ファイル特定・構造化抽出・キーワード前段フィルタ・新規ファイル生成 |
| **Claude**（意味的判断） | 候補からどれが正しい値かの確定・確信度の付与・要確認の判定 |
| **人**（承認） | マッピング情報の作成/承認・最終成果物の検証 |

GraphRAG の自動マッチングは、人が渡す**マッピング情報**（`keywords` による表記揺れの明示）と **Claude の意味判定**に置き換えている。

## モード一覧

| モード | 目的 |
|--------|------|
| `init` | 依存ライブラリのインストール（初回のみ） |
| `extract` | フォルダを部分一致で走査 → 元仕様書を構造化Markdown化 |
| `map-draft` | 自然文の対応記述ファイル → マッピングファイルのドラフト生成 |
| `validate` | マッピングファイルのスキーマ検証 |
| `find` | マッピング × 抽出結果 → 項目ごとの候補を抽出 |
| `fill` | 記入シート例 + 確定値 → 値を埋めた新規ファイルを生成 |

```
scripts/
├── run.py        # エントリポイント（__file__ 基準でパス解決）
├── models.py     # データ構造（Cell.path = breadcrumb）
├── extract.py    # Excel/Word/PowerPoint/PDF/Markdown/txt → 構造化抽出
├── mapping.py    # マッピングファイルのスキーマ・検証・ドラフト
├── finder.py     # キーワード前段フィルタ → 候補抽出
├── filler.py     # テンプレート + findings → 新規ファイル生成
└── requirements.txt
```

`run.py` は `__file__` 基準でパスを解決するため、スキルがどこに配置されていても動く。
コマンド例は `python` を使用する（環境により `python3`）。

---

## 標準ワークフロー

```
[init] ─→ [map-draft] ─→ 人がマッピング承認 ─→ [validate]
                                                    │
                              [extract] ←───────────┤
                                                    ▼
                                  [find] ─→ Claude が候補を吟味
                                                    │
                                            findings.json 作成
                                                    ▼
                                  [fill] ─→ 人が成果物を検証
```

### Step 0 — init（初回のみ）

```bash
python scripts/run.py init
```

### Step 1 — マッピング情報を用意する

マッピングファイル（`mapping.yaml`）は「記入先の項目」と「元仕様書での探し方」を結びつける辞書。`keywords` が表記揺れ対策の要になる。

```yaml
version: 1
source:
  folder: ./specs          # 元仕様書フォルダ（再帰走査）
  name_match: "HW仕様"      # ファイル名の部分一致パターン（空=全件）
items:
  - target: "MTU上限"                # 必須: 記入先の項目名
    keywords: ["MTU", "Maximum Transmission Unit", "最大転送単位"]  # 必須: 表記揺れを列挙
    section_hint: "ネットワーク"      # 任意: 章/シートのヒント（探索精度が上がる）
    unit: "bytes"                     # 任意: 期待単位
    type: number                      # 任意: number|text|enum|date
    note: "レイヤ2 のフレーム長"       # 任意: 補足
```

別ファイル（Excel等）に自然文で対応記述がある場合は `map-draft` でドラフトを起こす:

```bash
python scripts/run.py map-draft ./対応表.xlsx --out mapping.yaml
```

`map-draft` はスカフォールドと「元記述」を出力する。Claude は元記述を読んで `items` を埋め、人がレビューする。確定後に検証する:

```bash
python scripts/run.py validate mapping.yaml
```

### Step 2 — 元仕様書を確認する（任意）

全文を読みたいときは `extract` で Markdown 化する。

```bash
python scripts/run.py extract ./specs --name-match HW仕様 --out ./.svf-cache
```

`--out` を付けると各ファイルの `.md` / `.json` を出力。省略時は単一ファイルなら標準出力に Markdown を表示する。

### Step 3 — find（候補を抽出する）

```bash
python scripts/run.py find --mapping mapping.yaml --out candidates.json
```

各項目について、`keywords` に一致したセル/段落を **値・出典・breadcrumb・行文脈** 付きで列挙する。`source` はマッピングから読むが `--folder` / `--name-match` で上書きできる。

### Step 4 — Claude が候補を吟味して findings.json を作る（中核）

**ここがスキルの中核。`find` の出力 `candidates.json` を Claude が読み、項目ごとに値を確定する。** `candidates[0]` を機械的に採用してはならない。

各候補について次を踏まえて判断する:

- `path`（breadcrumb）と `section_hint` が意味的に整合しているか
- セル一致のとき、欲しい値は一致セルそのものか、`row_values` の隣接セルか
- `unit` / `type` がマッピングの期待と一致しているか
- `matched_keywords` / `score` はあくまでヒント。最終判断は意味で行う

確信度の基準:

| 確信度 | 目安 |
|--------|------|
| `high` | breadcrumb・単位・型がすべて整合し、候補が一意 |
| `medium` | 値は見つかるが breadcrumb か単位の確認が必要 |
| `low` | 候補が複数競合 / 単位・型が不一致 / 候補なし |

`findings.json` の形式:

```json
{
  "items": [
    {
      "target": "MTU上限",
      "value": "1500",
      "source": "HW仕様書_v2.xlsx :: シート'Network' :: C2",
      "confidence": "high",
      "needs_review": false,
      "comment": ""
    }
  ]
}
```

`confidence` が `high` 以外、または単位・型の不一致がある項目は `needs_review: true` にする。候補が見つからない項目も `value: ""` / `needs_review: true` で必ず含める（人が気づけるように）。

### Step 5 — fill（新規ファイルを生成する）

記入シート例（テンプレート）を複製し、確定値を埋めた新規ファイルを出力する。

```bash
python scripts/run.py fill --template 記入例.xlsx --findings findings.json --out 結果.xlsx
```

**Excel テンプレート**: 「項目」列の項目名と `target` を突合して各行に記入する。期待する見出し: `項目` / `値` / `出典` / `確信度` / `要確認`（見出し名は `--col-item` などで変更可）。テンプレートに無い項目は末尾に追記される。

**Word テンプレート**: 本文・表中の `{{項目名}}` プレースホルダを値で置換する。

生成後、人が `出典` と `要確認` 列を見て検証する。

---

## マッピングファイル スキーマ

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `version` | 推奨 | スキーマバージョン（`1`） |
| `source.folder` | 推奨 | 元仕様書フォルダ（再帰走査） |
| `source.name_match` | 任意 | ファイル名の部分一致パターン |
| `items[].target` | **必須** | 記入先の項目名 |
| `items[].keywords` | **必須** | 元仕様書での表記揺れを列挙（1個以上） |
| `items[].section_hint` | 任意 | 章/シートのヒント。score にボーナス加点される |
| `items[].unit` / `type` / `note` | 任意 | Claude の確信度判断に使う |

---

## 対応フォーマット（元仕様書）

| 種別 | 拡張子 | 抽出方式 |
|------|--------|---------|
| Excel | `.xlsx` `.xlsm` | openpyxl。結合セルを展開し表構造の breadcrumb を保持 |
| Word | `.docx` | python-docx。見出し階層 ＋ 表 ＋ 本文段落 |
| PowerPoint | `.pptx` | python-pptx。スライドタイトル ＋ 図形テキスト ＋ 表 |
| PDF | `.pdf` | pypdfium2。透明テキスト層をページ単位で抽出（表構造解析はしない） |
| Markdown | `.md` `.markdown` | 見出し階層 ＋ パイプ表 ＋ 段落 |
| テキスト | `.txt` | 空行区切りの段落 |

- PDF はテキスト層からの抽出のみ。**スキャン画像だけで本文テキスト層を持たない PDF は抽出が空になる**（OCR は行わない）。
- 旧形式（`.xls` / `.doc` / `.ppt`）は非対応。新形式に変換して使う。
- 記入シート例（`fill` のテンプレート）は Excel / Word に対応する。

---

## エラー対処

| 現象 | 対処 |
|------|------|
| `No module named openpyxl` / `pypdfium2` 等 | `init` を実行 |
| `find` で候補0件 | マッピングの `keywords` に表記揺れを追加。`extract` で実テキストを確認 |
| PDF を `extract` しても段落0件 | テキスト層が無い（スキャンPDF等）。本文テキスト層付きPDFか別形式に変換する |
| 候補は出るが値がずれる | `row_values` の隣接セルが正解。Claude が行文脈から値を選ぶ |
| `fill` で「項目列見出しが見つかりません」 | テンプレートの見出し名を `--col-item` 等で指定 |
| マッピング検証 NG | `validate` のエラーメッセージに従い `target` / `keywords` を修正 |
