---
name: obsidian-use
description: "「Obsidianのノートを作って」「ウィキリンクを追加して」「コールアウトを書いて」「フロントマターを設定して」「タグをつけて」「ファイルを埋め込んで」「.baseファイルでフィルターやフォーミュラを使って」「キャンバスやマインドマップやフローチャートを作って」「ボルトをCLIで操作して」「プラグインを開発して」「このURLを読んで」「OfficeファイルをMarkdownに変換して」「WordファイルをObsidianに取り込んで」「PDFをノートに変換して」などで発動する。"
metadata:
  version: "1.0.0"
---

# Obsidian Use

Obsidian に関連するすべての操作をカバーする統合スキル。5つの機能領域を提供する。

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

### Defuddle（Webコンテンツ抽出）

`defuddle` CLIでWebページから不要な要素（ナビゲーション・広告など）を除去し、クリーンなMarkdownを抽出する。URLを読む際はWebFetchよりもトークン効率が良い（.mdファイルには使わない）。

詳細: [references/defuddle.md](references/defuddle.md)

### markitdown（Officeファイル変換）

`markitdown` CLIでOfficeファイル（Word・Excel・PowerPoint・PDF等）をMarkdownに変換し、Obsidian Flavored Markdownに整形する。変換後にフロントマター追加・ウィキリンク化・コールアウト変換・画像埋め込み化を行う。

詳細: [references/markitdown.md](references/markitdown.md)
