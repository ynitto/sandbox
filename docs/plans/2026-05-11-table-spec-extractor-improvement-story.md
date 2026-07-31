# table-spec-extractor 改善ストーリー

## なぜ改善が必要か

table-spec-extractor は Excel や PDF に書かれた仕様表を Neo4j グラフに取り込み、
GraphRAG 検索を可能にするツールだ。
運用が進むにつれ、次の 3 つの問題が顕在化してきた。

1. **大量のシートを含む Excel を取り込むと著しく遅い** — 実用に耐えない待ち時間が発生する
2. **仕様書の管理粒度が粗い** — 仕様書の種別・ファイル名という重要属性が欠落しており、製品間比較や検索の精度が低い
3. **同じファイルを再インポートしても更新にならない** — 仕様書は継続的に更新されるが、再実行するたびに重複ノードが増殖する

これらを一気に解消し、日常的に使える信頼性の高いパイプラインにする。

---

## 改善の全体像

| # | 分類 | タイトル | 状態 |
|---|------|---------|------|
| P-1 | パフォーマンス | N+1 クエリ → UNWIND バルク書き込み | ✅ 実装済み |
| P-2 | パフォーマンス | 階層列検出の O(n²) ループ解消 | ✅ 実装済み |
| F-1 | 機能 | 仕様書種別によるデータベース分離 | ✅ 実装済み |
| F-2 | 機能 | ファイル名を仕様書の属性として管理 | ✅ 実装済み |
| F-3 | 機能 | 同一ファイル再インポート時のデータ更新 | ✅ 実装済み |

---

## P-1: N+1 クエリ問題の解消

**対象ファイル**: `scripts/graph_loader.py`

### 問題

`_write_table` が行・セル・リレーションシップを 1 件ずつ `tx.run()` で発行していた。
100 行 × 10 列のテーブルで約 4,200 回のネットワーク往復が発生し、テーブル数に比例して線形に悪化する。

### 解決

`UNWIND` を使ったバルク操作に置き換え、同じテーブルを 8 クエリで処理するように変更した。

| 操作 | 修正前 | 修正後 |
|------|--------|--------|
| MERGE Row + HAS_ROW + NEXT_ROW | ~300 | 3 |
| MERGE Cell + HAS_CELL + NEXT_CELL | ~3,000 | 3 |
| SAME_COLUMN 関係 | ~900 | 1 |
| Table + CONTAINS | 1 | 1 |
| **合計** | **~4,200** | **8** |

---

## P-2: 階層列検出の O(n²) ループ解消

**対象ファイル**: `scripts/ast_builder.py`

### 問題

`_infer_paths` 内で列ごとに全データ行を再スキャンしており、内側の `any()` がさらにセルを走査するため実質 O(rows × cols²) になっていた。加えて、セルごとに `sorted(hierarchy_cols)` を毎回再計算していた。

### 解決

- `col_idx → cells` の辞書を一度だけ構築して O(n) に削減
- `sorted_hierarchy` をループ外で一度だけ計算してキャッシュ

---

## F-1: 仕様書種別によるデータベース分離

**対象ファイル**: `scripts/run.py`, `scripts/models.py`, `scripts/graph_loader.py`

### 問題

すべての仕様書が同一 DB に混在するため、ハードウェア仕様とソフトウェア仕様を比較するグラフトラバーサルにノイズが混入していた。

### 解決

`save` コマンドに `--doc-type` オプションを追加した。省略した場合はインタラクティブに入力を求める。種別はそのまま Neo4j のデータベース名として使われ、既存の `--database` 指定がある場合はそちらを優先する。

```
# 種別を明示して保存
python run.py save spec.xlsx --doc-type hw-spec

# 省略するとプロンプトが出る
python run.py save spec.xlsx --profile local
仕様書の種別を入力してください (例: hw-spec, sw-spec, if-spec): hw-spec
```

`Document` ノードには `doc_type` プロパティが追加され、種別をまたいだ横断検索時のフィルタキーとしても使える。

---

## F-2: ファイル名を仕様書の属性として管理

**対象ファイル**: `scripts/models.py`, `scripts/ast_builder.py`, `scripts/graph_loader.py`

### 問題

`Document.source` にはフルパスが入っており、ファイルの配置場所が変わると値が変わってしまう。ファイル名（ステム）での検索・フィルタもできない状態だった。

### 解決

`Document` に `filename`（ファイル名）と `file_stem`（拡張子なし名）を追加した。
Neo4j 側でも同名プロパティに格納し、`doc_filename` インデックスを作成して高速フィルタを可能にした。

```cypher
MATCH (d:Document {filename: "hw_spec_v2.xlsx"}) RETURN d
```

---

## F-3: 同一ファイル再インポート時のデータ更新

**対象ファイル**: `scripts/models.py`, `scripts/ast_builder.py`, `scripts/graph_loader.py`, `scripts/run.py`

### 問題

`node_id` が毎回 `uuid4()` で生成されるため、同じファイルを再インポートすると既存ノードを更新せず重複ノードを増殖させていた。

### 解決

#### 決定論的 ID

ファイルパスと構造的な位置情報から SHA-256 ハッシュで `node_id` を生成するようにした。
同じ位置にある行・セルは常に同じ `node_id` を持つため、`MERGE` が更新として機能する。

| ノード | ID 生成キー |
|--------|------------|
| `Document` | `resolve(source_path)` |
| `Section` | `doc_id + title + page` |
| `Table` | `section_id + sheet + page` |
| `Row` | `table_id + row_index` |
| `Cell` | `row_id + col_idx` |

#### `--overwrite` フラグ

行・列が削除された場合、旧ノードが孤立ノードとして残る問題がある。
`--overwrite` を指定すると再投入前に既存の Document サブグラフを `DETACH DELETE` し、
常に最新状態だけがグラフに残るようにした。

```
# 初回
python run.py save spec.xlsx --profile local --doc-type hw-spec

# ファイル更新後に再インポート（既存データを置き換え）
python run.py save spec.xlsx --profile local --doc-type hw-spec --overwrite
```

### トレードオフ

| 観点 | 内容 |
|------|------|
| シート名変更時 | Section の ID が変わるため新規扱いになる。`--overwrite` で旧 Section は削除される |
| 並列インポート | 同一ファイルの同時インポートは競合する。アプリレベルのロックは別途必要 |
| 既存データ | ランダム UUID で登録済みのデータとは `node_id` が異なるため共存する。必要に応じて再インポートで移行する |

---

## 関連 PR

- [#314 perf: fix Excel import speed in table-spec-extractor](https://github.com/ynitto/sandbox/pull/314)
