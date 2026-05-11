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
