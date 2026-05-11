# table-spec-extractor Excel インポート速度改善

## 背景

大量のシートを含む Excel ファイルを取り込む際、処理に著しく時間がかかる問題があった。
調査の結果、Neo4j への書き込みと AST 構築の両フェーズにボトルネックを確認し修正した。

---

## 問題1: N+1 クエリ問題（最重大）

**ファイル**: `scripts/graph_loader.py` — `_write_table` メソッド

### 原因

行・セル・リレーションシップをそれぞれ個別の `tx.run()` で発行していたため、
テーブルサイズに比例してクエリ数が爆発的に増加していた。

100行 × 10列のテーブルで発生するクエリ数（修正前）:

| 操作 | クエリ数 |
|------|---------|
| MERGE Row | 100 |
| HAS_ROW 関係 | 100 |
| NEXT_ROW 関係 | 99 |
| MERGE Cell | 1,000 |
| HAS_CELL 関係 | 1,000 |
| NEXT_CELL 関係 | 990 |
| SAME_COLUMN 関係 | ~900 |
| **合計** | **~4,189** |

各クエリにはネットワーク往復・接続プール確保・Neo4j サーバー側のパース処理が伴うため、
テーブルが増えるほど線形に悪化する。

### 修正

`UNWIND` を使ったバルク操作に置き換え、同じテーブルを **8クエリ** で処理するよう変更した。

```cypher
-- 例: 全 Row を一括 MERGE
UNWIND $rows AS r
MERGE (n:Row {node_id: r.node_id})
SET n.index = r.index, n.is_header = r.is_header
```

修正後のクエリ数:

| 操作 | クエリ数 |
|------|---------|
| MERGE Row（全行一括） | 1 |
| HAS_ROW 関係（全行一括） | 1 |
| NEXT_ROW 関係（全行一括） | 1 |
| MERGE Cell（全セル一括） | 1 |
| HAS_CELL 関係（全セル一括） | 1 |
| NEXT_CELL 関係（全セル一括） | 1 |
| SAME_COLUMN 関係（全列一括） | 1 |
| MERGE Table + CONTAINS | 1 |
| **合計** | **8** |

---

## 問題2: 階層列検出の O(n²) ループ

**ファイル**: `scripts/ast_builder.py` — `_infer_paths` 関数

### 原因

#### (a) 全行を列ごとに再スキャン

列の空白率を調べる際、各列について全データ行を線形スキャンしていた。
列数 × 行数 のネストになるため O(rows × cols) だが、内側の `any()` がさらにセルを走査するため実質 O(rows × cols²)。

```python
# 修正前: 列ごとに全行を再スキャン
for ci in all_cols:
    empty = sum(
        1 for r in data_rows
        if any(c.col_idx == ci and not c.text.strip() for c in r.cells)  # O(cols)
    )
```

#### (b) 内側ループで毎回 `sorted()` を再計算

データ行 × セル数のループ内で `sorted(hierarchy_cols)` を毎回呼び出していた。
`hierarchy_cols` はループ中に変化しないにもかかわらず、セルごとにソートのコストが発生していた。

```python
# 修正前: セルごとに再ソート
for row in data_rows:
    for cell in row.cells:
        ...
        for hcol in sorted(hierarchy_cols):  # 毎回ソート
```

### 修正

#### (a) `col_idx → cells` の辞書を一度だけ構築

全セルを一度走査して辞書を作り、列ごとの空白チェックを O(n) で実行。

```python
col_to_cells: dict[int, list[Cell]] = {}
for r in data_rows:
    for c in r.cells:
        col_to_cells.setdefault(c.col_idx, []).append(c)

for ci, cells in col_to_cells.items():
    empty = sum(1 for c in cells if not c.text.strip())
    if n > 1 and empty / n > 0.3:
        hierarchy_cols.add(ci)
```

#### (b) `sorted()` をループ外に移動

```python
sorted_hierarchy = sorted(hierarchy_cols)  # 一度だけ計算

for row in data_rows:
    for cell in row.cells:
        ...
        for hcol in sorted_hierarchy:  # キャッシュ済みリストを参照
```

---

## 修正サマリー

| # | ファイル | 問題 | 修正 | 効果 |
|---|---------|------|------|------|
| 1 | `graph_loader.py` | 行・セルごとに個別クエリ発行 | UNWIND バルク操作に変更 | クエリ数 ~4,200 → 8（100行×10列の場合） |
| 2 | `ast_builder.py` | 列ごとに全行を再スキャン | col→cells 辞書で O(n) 化 | 計算量 O(rows×cols²) → O(rows×cols) |
| 3 | `ast_builder.py` | セルごとに `sorted()` 再計算 | ループ外で一度だけ実行 | 不要なソートを排除 |

## 関連 PR

- [#314 perf: fix Excel import speed in table-spec-extractor](https://github.com/ynitto/sandbox/pull/314)

---

# 機能改善提案

## 概要

以下 3 点の機能追加を提案する。いずれもコアデータモデル（`models.py`）および
入力パイプライン（`run.py` → `ast_builder.py` → `graph_loader.py`）の変更を伴う。

| # | タイトル | 動機 |
|---|---------|------|
| F-1 | 仕様書種別によるデータベース分離 | 種別をまたいだ仕様比較ができるようにしたい |
| F-2 | ファイル名を仕様書の属性として管理 | 製品ごとに仕様書ファイルが異なるため、ファイル名を検索・フィルタキーにしたい |
| F-3 | 同一ファイル再インポート時のデータ更新 | 複数製品の仕様が混在するファイルが継続的に更新されていくケースに対応したい |

---

## F-1: 仕様書種別によるデータベース分離

### 背景・動機

現状すべての仕様書が同一の Neo4j データベース（デフォルト `"neo4j"`）に格納される。
仕様書の「種別」（例: ハードウェア仕様 / ソフトウェア仕様 / インタフェース仕様）ごとに
データを分けることで、種別内でのグラフトラバーサルや比較検索を高精度にできる。

### 設計案

#### オプション A: Neo4j データベースを種別ごとに分ける（推奨）

Neo4j は 1 インスタンスに複数のデータベースを持てる。
`--database` CLI フラグが既に存在するため、種別をデータベース名にマッピングするだけで実現できる。

```
python run.py save spec.xlsx --doc-type hw-spec
# → Neo4j database "hw-spec" に格納
```

| 項目 | 内容 |
|------|------|
| 前提 | Neo4j Community 4.x 以上（複数 DB はデフォルトで利用可能） |
| 分離レベル | DB レベル（インデックス・フルテキスト検索もDB単位） |
| 比較クエリ | 別 DB 間の比較は `USE` 句では行えないため、Python 側で 2 セッション開いてマージ |

#### オプション B: 同一 DB 内で `doc_type` ラベル／プロパティで分離

Enterprise 不要・実装が簡単。ただし検索ノイズが増える。

```cypher
// B案: Document ノードに doc_type プロパティ
MERGE (d:Document {node_id: $node_id})
SET d.doc_type = $doc_type
```

**推奨: オプション A**（種別が明確に異なる場合は DB 分離が検索品質向上に直結する）

### 変更箇所

| ファイル | 変更内容 |
|---------|---------|
| `run.py` | `save` サブコマンドに `--doc-type` 引数を追加。省略時はインタラクティブに入力を求める |
| `run.py` | `--doc-type` の値を `--database` のデフォルト値として使用 |
| `models.py` | `Document.doc_type: str = ""` フィールド追加 |
| `graph_loader.py` | `_MERGE_DOCUMENT` クエリに `doc_type` プロパティを追加 |
| `config.py` | プロファイルに `default_doc_type` を追加（省略時の既定値） |

### インタラクティブ入力フロー（`--doc-type` 省略時）

```
$ python run.py save spec.xlsx --profile local
仕様書の種別を入力してください [hw-spec / sw-spec / if-spec / その他]: hw-spec
[1/3] 解析中: spec.xlsx …
```

### 未解決事項

- 種別をあらかじめ列挙するか、自由文字列にするか（タイポリスク vs 柔軟性）
- 種別をまたいだ横断検索 API の設計（`search` サブコマンドへの `--databases` 複数指定）

---

## F-2: ファイル名を仕様書の属性として管理

### 背景・動機

現状 `Document.source` にはファイルの絶対パスが入っているが、
ファイルの配置場所が変わると `source` が変わってしまう。
また、ファイル名（ステム）だけで検索・フィルタしたいユースケースが多い。

### 設計案

`Document` ノードに `filename`（ファイル名のみ）と `file_stem`（拡張子なし）を追加する。

```python
# models.py
@dataclass
class Document:
    source: str          # 変更なし（フルパス）
    filename: str = ""   # 追加: Path(source).name
    file_stem: str = ""  # 追加: Path(source).stem
    ...
```

```cypher
// graph_loader.py — _MERGE_DOCUMENT
MERGE (d:Document {node_id: $node_id})
SET d.source   = $source,
    d.filename = $filename,
    d.file_stem = $file_stem,
    d.metadata  = $metadata
```

`filename` にインデックスを追加することで、ファイル名による高速フィルタが可能になる。

```cypher
CREATE INDEX doc_filename IF NOT EXISTS FOR (n:Document) ON (n.filename)
```

### 変更箇所

| ファイル | 変更内容 |
|---------|---------|
| `models.py` | `Document` に `filename`, `file_stem` フィールド追加 |
| `ast_builder.py` | `build_from_excel` / `build_from_pdf` で `Path(path).name`, `Path(path).stem` をセット |
| `graph_loader.py` | `_MERGE_DOCUMENT` クエリに `filename`, `file_stem` を追加 |
| `graph_loader.py` | `create_indexes()` に `doc_filename` インデックスを追加 |
| `search.py` | 検索クエリに `filename` フィルタオプションを追加 |
| `run.py` | `save` に `--filename-alias` オプション追加（ファイル名を任意の表示名に上書きできる） |

### 表示名エイリアスの活用例

```
python run.py save ./specs/v2.3/hw_spec_20260501.xlsx \
    --filename-alias "HW仕様書 v2.3"
```

グラフ上では `filename = "HW仕様書 v2.3"` として管理され、バージョン比較がしやすくなる。

---

## F-3: 同一ファイル再インポート時のデータ更新

### 背景・動機

現状 `node_id` は毎回 `uuid4()` で生成されるため、同じファイルを再インポートすると
既存ノードを更新せず重複ノードを作り続ける。
同一ファイルに複数製品の仕様が書かれており継続的に更新されるケースでは、
再インポートで最新状態に上書きしたい。

### 問題の核心

```python
# models.py — 現状
node_id: str = field(default_factory=_uid)  # 毎回ランダム UUID
```

`node_id` がランダムである限り、`MERGE (d:Document {node_id: $node_id})` は
常に新規ノードを作成する。

### 設計案: コンテンツベースの決定論的 ID

ファイルパスと構造的な位置情報からハッシュで `node_id` を生成する。
ファイルが更新されても「同じ位置にある行・セル」は同じ `node_id` になるため
`MERGE` が更新として機能する。

```python
# models.py に追加するユーティリティ
import hashlib

def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
```

| ノード | ID 生成キー |
|--------|------------|
| `Document` | `normalize(source_path)` |
| `Section` | `doc_id + section_title + str(page)` |
| `Table` | `section_id + (sheet or "") + str(page)` |
| `Row` | `table_id + str(row_index)` |
| `Cell` | `row_id + str(col_idx)` |

### 削除済みデータのクリーンアップ

行数や列数が変わった場合、旧ノードが孤立ノードとして残る。
再インポート時に旧 Document サブグラフを削除してから再投入する戦略を取る。

```cypher
-- 再インポート前に旧サブグラフを削除（cascade）
MATCH (d:Document {node_id: $doc_id})
OPTIONAL MATCH (d)-[:HAS_SECTION*..5]->(n)
DETACH DELETE d, n
```

これにより削除された行・セルが残らず、常に最新状態がグラフに反映される。

### 変更箇所

| ファイル | 変更内容 |
|---------|---------|
| `models.py` | `_stable_id()` 関数を追加。各クラスの `node_id` デフォルト生成を `field(default_factory=_uid)` のまま維持し、stable ID を外部から注入できる設計にする |
| `ast_builder.py` | `build_from_excel` / `build_from_pdf` でノード生成時に `_stable_id()` で `node_id` をセット |
| `graph_loader.py` | `load()` メソッドに `overwrite: bool = False` 引数を追加。`True` の場合は既存 Document サブグラフを `DETACH DELETE` してから再投入 |
| `run.py` | `save` サブコマンドに `--overwrite` フラグを追加 |

### 利用イメージ

```
# 初回インポート
python run.py save spec.xlsx --profile local

# ファイル更新後に再インポート（既存データを置き換え）
python run.py save spec.xlsx --profile local --overwrite
```

### トレードオフ

| 観点 | 内容 |
|------|------|
| ID 衝突リスク | SHA-256 の先頭 32 文字を使用。実用上の衝突は無視できる |
| シート名変更時 | Section の ID が変わるため新規扱いになる。旧 Section は `--overwrite` で削除される |
| 並列インポート | 同一ファイルの同時インポートは競合する。アプリレベルのロックを別途設ける必要がある |
| 後方互換性 | 既存のランダム UUID で登録済みのデータは別 `node_id` を持つため共存する。マイグレーション手順が必要 |
