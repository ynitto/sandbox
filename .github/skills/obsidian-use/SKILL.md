---
name: obsidian-use
description: "「Obsidianのノートを作って」「.baseでフィルターを設定して」「キャンバスを作って」「このURLを読んで」「PDFをMarkdownに変換して」などのノート操作と、「Obsidianのwikiに取り込んで」「Obsidianのwikiで調べて」「wikiを点検して」の知識wiki管理、フックの OBSIDIAN NOTE で発動する。"
metadata:
  version: "1.1.0"
  tier: domain
  category: knowledge-management
  tags:
    - obsidian
    - markdown
    - note-taking
    - knowledge-base
    - llm-wiki
    - hooks
---

# Obsidian Use

Obsidian に関連するすべての操作をカバーする統合スキル。ノートの記法・CLI・取り込みに加え、ボルトを知識 wiki として育て、
セッションのフックでプロジェクト・環境のメモを集める。

## 前提条件（外部ツール）

各機能の利用前にツールがインストールされていることを確認する:

| ツール | 用途 | インストール |
|--------|------|-------------|
| Obsidian CLI (`obsidian`) | ボルト操作・wiki の検索・プラグイン開発 | Obsidian 1.12 以降に同梱。設定 → 一般 → コマンドラインインターフェース で有効にする（Obsidian が起動している必要あり） |
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
| ボルトを知識 wiki にする（init / ingest / query / save / lint） | [references/wiki.md](references/wiki.md) |
| フックでメモを集める（OBSIDIAN NOTE・導入・調整） | [references/memo-hooks.md](references/memo-hooks.md) |

## ナレッジ wiki

wiki-use と同じ「エージェントがソースを読んで知識ノートを育てる」使い方を、Obsidian のボルトの中で行う。
**検索・バックリンク・タグ・一覧・リンク切れの数え上げは Obsidian に任せ**、索引やスコアは持たない
（一覧は Bases の `index.base`、検索は Obsidian CLI の `search`。Obsidian が閉じているときだけ文字列一致に落ちる）。
手順は [references/wiki.md](references/wiki.md)。コマンドは `python scripts/vault.py <操作>`。

| 操作 | トリガー例 | コマンドの入口 |
|------|-----------|---------|
| **init** | 「Obsidian の wiki を作って」「ボルトを wiki にして」 | `vault.py init --vault <フォルダ>` |
| **ingest** | 「Obsidian の wiki に取り込んで」「この URL をボルトにまとめて」 | `vault.py search` → `vault.py note --scope knowledge` |
| **query** | 「wiki で調べて」「ボルトに何かある？」 | `vault.py search "<語>"` → ノートを読む → `obsidian backlinks` / `links` |
| **save** | 「この答えを wiki に残して」 | `vault.py note --type topic` |
| **lint** | 「wiki を点検して」 | `vault.py lint` |
| **memo** | **（フック）** 会話に `OBSIDIAN NOTE` の指示が出た | 指示に書かれた `vault.py memo …` |

ボルトの中は `knowledge/`（世界の知識）・`projects/<プロジェクト>/`（そのリポジトリだけのメモ）・
`envs/<ホスト-OS>/`（その PC だけのこと）に分かれる。設定は `python scripts/vault.py config` で確かめる。

### プロアクティブな操作

**設定済み（`vault.py config` が通る）なら、利用者に確認せずに行う。**

- 質問に答える前、またはエラーで詰まったときに、関係しそうなら `vault.py search "<語>"` を 1 回引く
  （このプロジェクトだけなら `--scope project`）。当たったノートを読んで答えに使う
- URL・文書を「読んで」「まとめて」と渡されたら、`search` で取り込み済みか確かめ、未取り込みなら ingest する
  （コード・設定ファイル・ログは対象外）
- 会話に `OBSIDIAN NOTE` が出たら、[references/memo-hooks.md](references/memo-hooks.md) のとおり役立つことだけを
  `vault.py memo` で残し、利用者への返事は「Obsidian: <パス>」の 1 行にする（無ければ何もしない）
- 会話に `[Obsidian]` で始まるメモの一覧が出たら、いまの作業に関係するものは中身を読んでから進める

### フック（メモを集める）

moltbook-use のフックと同じ作りで、書き先が自分のボルトになり、**プロジェクト・環境に固有のメモも残す**
（他の人に見せないので、パスやリポジトリ名を書いてよい。秘密情報は書かない）。ツールの失敗・回復・言い直し・
「メモして」・まとまった作業を控え、ターンの終わりに 1 回だけ振り返らせる。開始時には、このプロジェクトと
この環境の既存のメモの題を数行だけ会話に足す。

導入は Windows で `scripts/hook_install.ps1 -Vault <ボルト>`（Copilot アプリと WSL の kiro-cli）、WSL・Linux で
`sh scripts/hook_install.sh --vault <ボルト>`、Claude Code は `sh scripts/hook_install.sh install --client claude --vault <ボルト>`。
点検は `doctor`、外すときは `uninstall`。詳細は [references/memo-hooks.md](references/memo-hooks.md)、控える語と上限は [references/memo_rules.json](references/memo_rules.json)。

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
