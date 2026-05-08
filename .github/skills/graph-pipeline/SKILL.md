---
name: graph-pipeline
description: |
  Excel/PDFファイルをTable Transformer（microsoft/table-transformer-*）で解析し、
  Document ASTを構築してNeo4jグラフDBへ保存・GraphRAGクエリを実行するパイプライン。
  Excelの結合セルをcarry-forwardで階層フラット化し、各セルにbreadcrumb pathを付与。
  テーブルをMarkdown中間表現に変換してNeo4jに保存することでLLMからの参照を容易にする。
  init（依存インストール）、save（ドキュメント→Neo4j保存）、search（グラフ検索）、
  config（複数Neo4jプロファイル管理）の4モードを持つ。
  トリガー: /graph-pipeline、「PDFをグラフに保存」「Neo4jで検索」「ドキュメントグラフ」
  「テーブルを抽出してグラフに」「GraphRAGで検索」などのキーワード。
metadata:
  version: "2.0.0"
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

# ドキュメントグラフパイプライン

Excel/PDFを解析し、テーブル構造をNeo4jグラフとして蓄積・検索するパイプライン。
結合セルの階層構造を保持したまま保存できるため、「どの画面の・どの機能の・どの設定値か」という文脈を壊さずに管理できる。

## モード一覧

| モード | 目的 | 最初に使うか |
|--------|------|------------|
| `init` | 依存インストール＋Neo4j疎通確認 | 初回必須 |
| `save` | ドキュメント→AST→Neo4jへロード | ドキュメント追加時 |
| `search` | グラフ全文＋列コンテキスト付き検索 | 検索時 |
| `config` | 複数Neo4jプロファイル管理 | 接続先変更時 |

## スクリプト配置

```
.github/skills/graph-pipeline/scripts/
├── run.py                 # エントリポイント（sys.path自動設定）
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

実行はすべて以下のディレクトリを起点とする:
```bash
cd .github/skills/graph-pipeline/scripts
```

---

## configモード（複数Neo4jプロファイル管理）

保存先ごとに接続情報とローカルデータパスを設定できる。
設定ファイル: `~/.graph-pipeline/config.json`（`$GRAPH_PIPELINE_CONFIG`環境変数で上書き可）

```bash
# プロファイルを追加
python run.py config add local \
  --neo4j bolt://localhost:7687 \
  --user neo4j --password "" \
  --data-path ~/graph-data/local \
  --set-default

python run.py config add prod \
  --neo4j bolt://prod-server:7687 \
  --user neo4j --password secret \
  --data-path ~/graph-data/prod

# 一覧・詳細
python run.py config list
python run.py config show local

# デフォルト変更・削除
python run.py config set-default prod
python run.py config remove local
```

`data-path`: saveモード実行時にASTのJSONスナップショットを保存するディレクトリ。
Neo4jが不要な場合はここだけ設定してローカル保存のみも可能。

---

## initモード

依存ライブラリをインストールし、Neo4j接続を確認する。**初回に必ず実行する。**

```bash
# 依存インストールのみ
python run.py init

# プロファイルを使ってNeo4j疎通も確認
python run.py init --profile local

# URI直接指定
python run.py init --neo4j bolt://localhost:7687 --password secret
```

---

## saveモード

Excel/PDFをTable Transformerで解析してNeo4jへロードする。
テーブルはMarkdown形式でも保存されるため、LLMが`markdown_text`プロパティを直接参照できる。

### Step 1 — dry-run（ASTを事前確認）

```bash
python run.py save <FILE> --dry-run
```

セクション数・テーブル数・段落数を確認する。テーブルが0件の場合はStep 1で止まり、
`--dpi 200`（解像度向上）や `--threshold 0.7`（検出感度向上）を提案する。

### Step 2 — Neo4jへロード

```bash
# プロファイル使用（data-pathへのスナップショット保存も自動実行）
python run.py save <FILE> --profile local

# URI直接指定
python run.py save <FILE> --neo4j bolt://localhost:7687 --password secret
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
python run.py search "検索クエリ" --profile local
python run.py search "売上" --neo4j bolt://localhost:7687 --limit 20
python run.py search "MaxConnections" --profile prod --json
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
