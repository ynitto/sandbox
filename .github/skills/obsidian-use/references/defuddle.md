# Defuddle

Defuddle CLIでWebページ・ローカルHTMLファイルからクリーンなコンテンツを抽出する。WebFetchよりもトークン効率が良いため、標準的なWebページやHTMLファイルにはDefuddleを優先して使う。`.md` URLにはWebFetchを直接使うこと。

未インストールの場合: `npm install -g defuddle`

## 入力ソースの種類

| 入力 | コマンド例 |
|------|-----------|
| URL | `defuddle parse https://example.com/article --md` |
| ローカルHTMLファイル | `defuddle parse article.html --md` |

## 使い方

Markdown出力には常に `--md` を使用する:

```bash
# URLを解析
defuddle parse <url> --md

# ローカルHTMLファイルを解析
defuddle parse <file.html> --md
```

ファイルに保存:

```bash
defuddle parse <url-or-file.html> --md -o content.md
```

特定のメタデータを抽出:

```bash
defuddle parse <url-or-file.html> -p title
defuddle parse <url-or-file.html> -p description
defuddle parse <url-or-file.html> -p domain
```

## 出力フォーマット

| フラグ | フォーマット |
|--------|------------|
| `--md` | Markdown（推奨） |
| `--json` | HTMLとMarkdownの両方を含むJSON |
| （なし） | HTML |
| `-p <名前>` | 特定のメタデータプロパティ |

## ファイル種別の選択基準

| ファイル種別 | 使うツール |
|-------------|-----------|
| `.html` / `.htm`（ローカル） | **defuddle**（このページ） |
| URL（http/https） | **defuddle**（このページ） |
| `.docx` / `.xlsx` / `.pptx` / `.pdf` | **markitdown** → [markitdown.md](markitdown.md) |
| `.md` ファイルのURL | WebFetch を直接使う |
