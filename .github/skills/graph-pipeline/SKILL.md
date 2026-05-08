---
name: graph-pipeline
description: |
  Excel/PDFファイルをTable Transformer（microsoft/table-transformer-*）で解析し、
  Document ASTを構築してNeo4jグラフDBへ保存・GraphRAGクエリを実行するパイプライン。
  init（依存ライブラリインストール・Neo4j疎通確認）、save（ドキュメント→Neo4j保存）、
  search（グラフ全文＋トラバーサル検索）の3モードを持つ。
  トリガー: /graph-pipeline、「PDFをグラフに保存」「Neo4jで検索」「ドキュメントグラフ」
  「テーブルを抽出してグラフに」「GraphRAGで検索」などのキーワード。
metadata:
  version: "1.0.0"
  tier: experimental
  category: integration
  tags:
    - neo4j
    - graphrag
    - pdf
    - excel
    - table-transformer
    - document-graph
---

# ドキュメントグラフパイプライン

Excel/PDFをTable Transformerで解析し、テーブル構造をNeo4jグラフとして蓄積・検索するパイプライン。

## モード一覧

| モード | 目的 | 最初に使うか |
|--------|------|------------|
| `init` | 依存インストール＋Neo4j疎通確認 | 初回必須 |
| `save` | ドキュメント→AST→Neo4jへロード | ドキュメント追加時 |
| `search` | グラフ全文＋列コンテキスト付き検索 | 検索時 |

## スクリプト配置

```
.github/skills/graph-pipeline/scripts/pipeline/
├── models.py          # Document ASTノード定義
├── ingest.py          # PDF描画（pypdfium2）・Excel読込（openpyxl）
├── table_extractor.py # Table Transformer検出・構造認識
├── ast_builder.py     # ASTの組み立て
├── graph_loader.py    # Neo4j MERGE＋インデックス作成
├── search.py          # GraphRAGクエリ（全文＋SAME_COLUMNトラバーサル）
├── pipeline.py        # CLIエントリポイント
└── requirements.txt   # 依存ライブラリ一覧
```

実行はすべて以下のディレクトリを起点とする:
```bash
cd .github/skills/graph-pipeline/scripts
```

---

## initモード

依存ライブラリをインストールし、Neo4j接続を確認する。**初回に必ず実行する。**

```bash
# 依存インストールのみ
python -m pipeline.pipeline init

# Neo4j疎通確認も同時に行う
python -m pipeline.pipeline init \
  --neo4j bolt://localhost:7687 \
  --user neo4j --password <PASS>
```

期待する出力:
```
[✓] 依存ライブラリ: OK
[✓] Neo4j 接続: OK (neo4j@bolt://localhost:7687)
```

エラー時は「依存ライブラリ失敗」と「Neo4j接続失敗」を区別して報告する。

---

## saveモード

Excel/PDFをTable Transformerで解析してNeo4jへロードする。

### Step 1 — dry-run（ASTを事前確認）

```bash
python -m pipeline.pipeline save <FILE> --dry-run
```

セクション数・テーブル数・段落数を確認する。テーブルが0件の場合はStep 1で止まり、
`--dpi 200`（解像度向上）や `--threshold 0.7`（検出感度向上）を提案する。

### Step 2 — Neo4jへロード

```bash
python -m pipeline.pipeline save <FILE> \
  --neo4j bolt://localhost:7687 \
  --user neo4j --password <PASS>
```

### saveオプション

| オプション | デフォルト | 説明 |
|-----------|-----------|------|
| `--dpi` | 150 | PDF描画解像度（高いほど検出精度向上・低速） |
| `--threshold` | 0.9 | テーブル検出信頼度（下げると検出数増加） |
| `--device` | cpu | Torchデバイス（`cuda` 指定で高速化） |
| `--dry-run` | — | Neo4jへロードせずASTをJSON表示 |

---

## searchモード

Neo4jグラフを全文検索＋グラフトラバーサルで検索する。
セル検索では同一列のヘッダーセルをコンテキストとして付与する（GraphRAG）。

```bash
python -m pipeline.pipeline search "検索クエリ" \
  --neo4j bolt://localhost:7687 \
  --user neo4j --password <PASS> \
  --limit 10
```

JSON出力が必要な場合:
```bash
python -m pipeline.pipeline search "売上" ... --json
```

出力例:
```
Search: "売上 Q3"
=== Table Cells (3) ===
  report.pdf › Page 5 › page 4 [売上高] (row 3, col 1)
    1,234,567
=== Paragraphs (1) ===
  report.pdf › Page 3 (page 2)
    ...Q3の売上は前年比...
```

ヒットが0件の場合はsaveモードでドキュメントがロード済みか確認する。

---

## グラフスキーマ（参考）

```
(:Document)-[:HAS_SECTION]->(:Section)
(:Section)-[:CONTAINS]->(:Table | :Paragraph)
(:Table)-[:HAS_ROW]->(:Row)
(:Row)-[:HAS_CELL]->(:Cell)
(:Row)-[:NEXT_ROW]->(:Row)
(:Cell)-[:NEXT_CELL]->(:Cell)   # 同行・左→右
(:Cell)-[:SAME_COLUMN]->(:Cell) # 同列・上→下（GraphRAGの要）
```

全文インデックス: `cell_fulltext`（Cell.text）、`para_fulltext`（Paragraph.text）

---

## エラー対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `No module named pypdfium2` | initが未実行 | `init`モードを実行 |
| テーブル0件 | 低解像度・高閾値 | `--dpi 200 --threshold 0.7` |
| `ServiceUnavailable` | Neo4j未起動 | Neo4jサービスを確認 |
| `index not found` | インデックス未作成 | `save`を一度実行してインデックスを作成 |
