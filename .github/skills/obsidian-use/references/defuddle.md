# Defuddle

Defuddle CLIでWebページからクリーンなコンテンツを抽出する。WebFetchよりもトークン効率が良いため、標準的なWebページにはDefuddleを優先して使う。`.md` URLにはWebFetchを直接使うこと。

未インストールの場合: `npm install -g defuddle`

## 使い方

Markdown出力には常に `--md` を使用する:

```bash
defuddle parse <url> --md
```

ファイルに保存:

```bash
defuddle parse <url> --md -o content.md
```

特定のメタデータを抽出:

```bash
defuddle parse <url> -p title
defuddle parse <url> -p description
defuddle parse <url> -p domain
```

## 出力フォーマット

| フラグ | フォーマット |
|--------|------------|
| `--md` | Markdown（推奨） |
| `--json` | HTMLとMarkdownの両方を含むJSON |
| （なし） | HTML |
| `-p <名前>` | 特定のメタデータプロパティ |
