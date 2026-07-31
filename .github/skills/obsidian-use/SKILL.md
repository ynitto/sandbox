---
name: obsidian-use
description: "「Obsidianのノートを作って」「ウィキリンクを追加して」「.baseファイルでフィルターを設定して」「マインドマップやキャンバスを作って」「ボルトをCLIで操作して」「このURLを読んで」「WordファイルやPDFをMarkdownに変換して」など、Obsidianの作成・編集・CLI操作・Web抽出・Officeファイル変換で発動する。"
metadata:
  version: "1.0.0"
  tier: domain
  category: knowledge-management
  tags:
    - obsidian
    - markdown
    - note-taking
    - knowledge-base
---

# Obsidian Use

Obsidian に関連するすべての操作をカバーする統合スキル。5つの機能領域を提供する。

## 前提条件（外部ツール）

各機能の利用前にツールがインストールされていることを確認する:

| ツール | 用途 | インストール |
|--------|------|-------------|
| Obsidian CLI (`obsidian`) | ボルト操作・プラグイン開発 | `npm install -g @anthropics/obsidian-cli`（Obsidianが起動している必要あり） |
| Defuddle (`defuddle`) | URL・HTMLファイル → Markdown 抽出 | `npm install -g defuddle` |
| markitdown (`markitdown`) | Office / PDF → Markdown 変換 | `pip install markitdown` |

## ファイル種別→ツール選択

渡されたファイルの種類によって使うツールを選択する:

| ファイル種別 | 使うツール | 参照 |
|-------------|-----------|------|
| `.html` / `.htm`（ローカルファイル） | **defuddle** | [references/defuddle.md](references/defuddle.md) |
| URL（http/https） | **defuddle** | [references/defuddle.md](references/defuddle.md) |
| `.docx` / `.xlsx` / `.pptx` | **markitdown → ofm_formatter.py → LLM** | [references/markitdown.md](references/markitdown.md) |
| `.pdf` | **markitdown → ofm_formatter.py → LLM** | [references/markitdown.md](references/markitdown.md) |
| `.md` ファイルのURL | WebFetch を直接使う | — |

## 機能領域の選択

| タスク | 参照 |
|--------|------|
| .md ファイルの作成・編集（ウィキリンク、コールアウト、埋め込み等） | [references/obsidian-markdown.md](references/obsidian-markdown.md) |
| .base ファイルの作成・編集（Bases、フィルター、フォーミュラ、ビュー） | [references/obsidian-bases.md](references/obsidian-bases.md) |
| .canvas ファイルの作成・編集（JSONキャンバス、ノード、エッジ） | [references/json-canvas.md](references/json-canvas.md) |
| Obsidian CLIコマンド（ボルト操作、ノート管理、プラグイン開発） | [references/obsidian-cli.md](references/obsidian-cli.md) |
| WebページからMarkdownを抽出（Defuddle） | [references/defuddle.md](references/defuddle.md) |
| OfficeファイルをObsidian Markdownに変換（markitdown） | [references/markitdown.md](references/markitdown.md) |

## 各機能の概要

### Obsidian Flavored Markdown（.md ファイル）

ObsidianはCommonMarkとGFMを独自記法で拡張している。主な機能:
- **ウィキリンク** (`[[ノート名]]`): ボルト内ノートへの内部リンク（名前変更時も自動追跡）
- **埋め込み** (`![[ファイル名]]`): ノート・画像・PDFをインライン表示
- **コールアウト** (`> [!type]`): 情報をハイライト表示するコンテナ
- **プロパティ** (YAMLフロントマター): タグ・エイリアス・カスタムCSSクラスなど

詳細: [references/obsidian-markdown.md](references/obsidian-markdown.md)

### Obsidian Bases（.base ファイル）

`.base` ファイルはYAMLで記述するデータベースライクなビュー。主な機能:
- **フィルター**: タグ・フォルダ・プロパティ・日付でノートを絞り込む
- **フォーミュラ**: プロパティから計算値を生成
- **ビュー**: table・cards・list・mapの4種類

詳細: [references/obsidian-bases.md](references/obsidian-bases.md)

### JSON Canvas（.canvas ファイル）

`.canvas` はJSON Canvas Spec 1.0に準拠したビジュアルキャンバス。主な機能:
- **ノード**: text/file/link/groupの4種類
- **エッジ**: ノード間の接続線（ラベル・矢印・色対応）
- 16文字hexのユニークIDでノードとエッジを識別

詳細: [references/json-canvas.md](references/json-canvas.md)

### Obsidian CLI

`obsidian` CLIでObsidianの実行中インスタンスを操作（Obsidianが開いている必要あり）。主な機能:
- ノートの読み取り・作成・追記・検索
- タスク・デイリーノート・プロパティ管理
- プラグイン・テーマ開発（リロード・デバッグ・スクリーンショット）

詳細: [references/obsidian-cli.md](references/obsidian-cli.md)

### Defuddle（URL・HTMLファイルのコンテンツ抽出）

`defuddle` CLIでWebページ・ローカルHTMLファイルから不要な要素（ナビゲーション・広告など）を除去し、クリーンなMarkdownを抽出する。URLやHTMLファイルを読む際はWebFetchよりもトークン効率が良い（.mdファイルには使わない）。

詳細: [references/defuddle.md](references/defuddle.md)

### markitdown（Office/PDFファイル変換）

`markitdown` CLIでOffice/PDFファイル（Word・Excel・PowerPoint・PDF等）をMarkdownに変換する。変換後に `ofm_formatter.py` で機械的後処理（フロントマター付加・画像パス変換・内部リンクのウィキリンク化）を行い、さらにLLMがコールアウト変換・タグ推定等の意味的整形を行う（2段階ハイブリッド方式）。HTMLファイルはdefuddleを使うこと。

詳細: [references/markitdown.md](references/markitdown.md) / スクリプト: [references/ofm_formatter.py](references/ofm_formatter.py)

## クロスドメインワークフロー例

### Web記事をObsidianノートに取り込む（URLの場合）

```bash
# 1. DefuddleでWebページをクリーンなMarkdownに変換
defuddle parse <url> --md -o article.md

# 2. ウィキリンクやフロントマターを整形（エージェントが意味的に加工）
# （フロントマター追加・固有名詞をウィキリンク化）

# 3. Obsidian CLI でボルトに保存
obsidian create name="記事タイトル" content="$(cat article.md)" silent
```

### HTMLファイルをObsidianノートに取り込む

```bash
# 1. DefuddleでローカルファイルをクリーンなMarkdownに変換
defuddle parse article.html --md -o article.md

# 2. エージェントが意味的整形（フロントマター追加・ウィキリンク化）

# 3. Obsidian CLI でボルトに保存
obsidian create name="記事タイトル" content="$(cat article.md)" silent
```

### Office/PDFファイルをObsidianノートに変換する

```bash
# 1. markitdownでOffice/PDFファイルをMarkdownに変換
markitdown document.docx -o raw.md

# 2. ofm_formatter.py で機械的後処理（フロントマター・画像パス・内部リンク）
python references/ofm_formatter.py raw.md note.md

# 3. エージェントがLLM意味的整形（コールアウト変換・タグ推定）
# （note.md を読んでコールアウト等を変換）

# 4. Obsidian CLI でボルトに保存
obsidian create name="ドキュメント名" content="$(cat note.md)" silent
```
