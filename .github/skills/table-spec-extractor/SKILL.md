---
name: table-spec-extractor
description: "Excel/PDFの仕様書テーブルをTable TransformerでAST化しNeo4jグラフへ保存・GraphRAG検索するパイプライン。「仕様書をグラフに保存して」「Neo4jで仕様を検索して」「テーブルをグラフ化して」「GraphRAGで検索して」などで発動。init/save/search/configの4モード。"
metadata:
  version: "2.2.0"
  tier: experimental
  category: integration
  tags:
    - neo4j
    - graphrag
    - pdf
    - excel
    - table-transformer
    - document-graph
    - markdown
---

# テーブル仕様抽出パイプライン

Excel/PDFの仕様書テーブルを解析し、Neo4jグラフとして蓄積・検索するパイプライン。
結合セルの階層構造を保持したまま保存できるため、「どの画面の・どの機能の・どの設定値か」という文脈を壊さずに管理できる。

> **Windows / macOS / Linux** 対応。コマンド例はすべて `python` を使用する（環境によっては `python3`）。

## モード一覧

| モード | 目的 | 最初に使うか |
|--------|------|------------|
| `init` | 依存インストール＋Neo4j疎通確認 | 初回必須 |
| `save` | ドキュメント→AST→Neo4jへロード | ドキュメント追加時 |
| `search` | グラフ全文＋列コンテキスト付き検索 | 検索時 |
| `config` | 複数Neo4jプロファイル管理 | 接続先変更時 |

## スクリプト配置

```
scripts/
├── run.py                 # エントリポイント（__file__ 基準でパス解決、どこから実行しても動作）
├── models.py              # Document ASTノード定義（Cell.path含む）
├── ingest.py              # PDF描画（pypdfium2）・Excel読込（openpyxl）
├── table_extractor.py     # Table Transformer検出・構造認識
├── ast_builder.py         # AST組み立て＋carry-forward breadcrumb推定
├── markdown_serializer.py # Table → Markdown変換（LLM向け中間表現）
├── graph_loader.py        # Neo4j MERGE＋インデックス作成
├── search.py              # GraphRAGクエリ（全文＋SAME_COLUMNトラバーサル）
├── config.py              # プロファイル管理（複数Neo4j対応）
└── requirements.txt       # 依存ライブラリ一覧
```

`run.py` は `__file__` を基準にパスを解決するため、スキルが `.github/skills/`・`~/.claude/skills/` など
どこに配置されていても動作する。

---

## configモード（複数Neo4jプロファイル管理）

保存先ごとに接続情報とローカルデータパスを設定できる。
設定ファイル: `~/.table-spec-extractor/config.json`（`TABLE_SPEC_EXTRACTOR_CONFIG` 環境変数で上書き可）

```bash
# プロファイルを追加
python scripts/run.py config add local \
  --neo4j bolt://localhost:7687 \
  --user neo4j --password "" \
  --data-path ~/graph-data/local \
  --set-default

python scripts/run.py config add prod \
  --neo4j bolt://prod-server:7687 \
  --user neo4j --password secret \
  --data-path ~/graph-data/prod

# 一覧・詳細
python scripts/run.py config list
python scripts/run.py config show local

# デフォルト変更・削除
python scripts/run.py config set-default prod
python scripts/run.py config remove local
```

`data-path`: saveモード実行時にASTのJSONスナップショットを保存するディレクトリ。
Neo4jが不要な場合はここだけ設定してローカル保存のみも可能。

---

## initモード

依存ライブラリをインストールし、Neo4j接続を確認する。**初回に必ず実行する。**

```bash
# 依存インストールのみ
python scripts/run.py init

# プロファイルを使ってNeo4j疎通も確認
python scripts/run.py init --profile local

# URI直接指定
python scripts/run.py init --neo4j bolt://localhost:7687 --password secret
```

---

## saveモード

Excel/PDFをTable Transformerで解析してNeo4jへロードする。
テーブルはMarkdown形式でも保存されるため、LLMが`markdown_text`プロパティを直接参照できる。

### Step 1 — dry-run（ASTを事前確認）

```bash
python scripts/run.py save <FILE> --dry-run
```

セクション数・テーブル数・段落数を確認する。テーブルが0件の場合はStep 1で止まり、
`--dpi 200`（解像度向上）や `--threshold 0.7`（検出感度向上）を提案する。

### Step 2 — Neo4jへロード

```bash
# プロファイル使用（data-pathへのスナップショット保存も自動実行）
python scripts/run.py save <FILE> --profile local

# URI直接指定
python scripts/run.py save <FILE> --neo4j bolt://localhost:7687 --password secret
```

### saveオプション

| オプション | デフォルト | 説明 |
|-----------|-----------|------|
| `--profile` | "" | 使用するプロファイル名 |
| `--dpi` | 150 | PDF描画解像度（高いほど検出精度向上・低速） |
| `--threshold` | 0.9 | テーブル検出信頼度（下げると検出数増加） |
| `--device` | cpu | Torchデバイス（`cuda` 指定で高速化） |
| `--data-path` | "" | ローカルスナップショット保存先（プロファイルを上書き） |
| `--dry-run` | — | Neo4jへロードせずASTをJSON表示 |

---

## searchモード

Neo4jグラフを全文検索＋グラフトラバーサルで検索する。
セル検索では同一列のヘッダーセルとbreadcrumb pathをコンテキストとして付与する。

```bash
python scripts/run.py search "検索クエリ" --profile local
python scripts/run.py search "売上" --neo4j bolt://localhost:7687 --limit 20
python scripts/run.py search "MaxConnections" --profile prod --json
```

ヒットが0件の場合はsaveモードでドキュメントがロード済みか確認する。

---

## グラフスキーマ

```
(:Document)-[:HAS_SECTION]->(:Section)
(:Section)-[:CONTAINS]->(:Table | :Paragraph)
(:Table {markdown_text})-[:HAS_ROW]->(:Row)
(:Row)-[:HAS_CELL]->(:Cell {text, path, is_header})
(:Row)-[:NEXT_ROW]->(:Row)
(:Cell)-[:NEXT_CELL]->(:Cell)    # 同行・左→右
(:Cell)-[:SAME_COLUMN]->(:Cell)  # 同列・上→下（GraphRAGの要）
```

**Cell.path** は breadcrumb 配列。例: `["Config", "Network", "IP Address"]`
**Table.markdown_text** はLLM向けMarkdown表現。

全文インデックス: `cell_fulltext`（Cell.text）、`para_fulltext`（Paragraph.text）

---

## ベクトル検索（将来の拡張）

Neo4j 5.x+ は vector index をサポートする。以下の手順で追加可能:
1. `sentence-transformers` などで Cell.text / Paragraph.text の埋め込みを生成
2. `CREATE VECTOR INDEX cell_vector FOR (n:Cell) ON (n.embedding) OPTIONS {indexConfig: {...}}`
3. `CALL db.index.vector.queryNodes(...)` で近傍検索

現時点では依存の重量（~500MB）から本スキルには含めていない。

---

## エラー対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `No module named pypdfium2` | initが未実行 | `init`モードを実行 |
| テーブル0件 | 低解像度・高閾値 | `--dpi 200 --threshold 0.7` |
| `ServiceUnavailable` | Neo4j未起動 | Neo4jサービスを確認 |
| `index not found` | インデックス未作成 | `save`を一度実行してインデックスを作成 |
| `KeyError: 'profiles'` | config.json形式不正 | `config list`で確認後、手動で修正 |
