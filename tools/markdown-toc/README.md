# markdown-toc

Markdown の ATX 見出しから目次を生成し、`<!-- toc -->` と `<!-- /toc -->` の間を更新する Python 3 CLI です。外部パッケージは不要です。

## 使い方

リポジトリのルートから実行します。

```sh
python3 tools/markdown-toc/markdown_toc.py README.md
python3 tools/markdown-toc/markdown_toc.py --create README.md
python3 tools/markdown-toc/markdown_toc.py --check README.md
python3 tools/markdown-toc/markdown_toc.py --stdout --min-level 2 --max-level 4 README.md
python3 tools/markdown-toc/markdown_toc.py - < README.md
```

## オプション

| 引数 | 説明 |
| --- | --- |
| `<file.md>` | 更新する UTF-8 ファイル。`-` を指定すると標準入力から読み、更新後の全文を標準出力へ出します。 |
| `--min-level N` | 目次に含める最小の見出しレベル。既定値は 2 です。 |
| `--max-level N` | 目次に含める最大の見出しレベル。既定値は 6 です。 |
| `--create` | マーカーが未挿入の場合、最初の ATX 見出しの直後にマーカーと目次を挿入します。 |
| `--check` | 入力を変更せず、目次が最新かを検査します。 |
| `--stdout` | ファイルを変更せず、更新後の全文を標準出力に出します。 |
| `-h`, `--help` | ヘルプを表示します。 |

## 仕様

- 見出しレベルの指定範囲は 1〜6 です。目次は最小レベルを基準に、1 レベルごとにスペース 2 個で字下げします。
- アンカーは見出しを小文字化し、空白をハイフンに置換します。文字、数字、結合文字、ハイフン、アンダースコアを保持します。重複時は `-1`、`-2` と番号を付けます。目次の対象範囲外の見出しもアンカーの重複判定に含めます。
- フェンス付きコードブロックと HTML コメント内の見出しを除外します。単独行に置かれた最初のマーカーの組を使い、その間を置き換えます。マーカー行は保持します。
- 新しい行には入力で最初に現れる改行コードを使います。CRLF と UTF-8 BOM は保持します。
- `--check` と `--stdout` は同時に指定できません。`--check --create` を指定した場合も、目次が未挿入なら終了コード 1 を返します。

## 既知の制限

- 対象は行頭からスペース 3 個までの ATX 見出しです。Setext 見出しや引用内の見出しは対象外です。
- 見出し全体の Markdown レンダリングは行わないため、リンク構文などを含む見出しのアンカーは GitHub 上のアンカーと異なる場合があります。
- マーカーの自動挿入には ATX 見出しが 1 つ以上必要です。

## 終了コード

| コード | 意味 |
| --- | --- |
| 0 | 正常終了。 |
| 1 | `--check` で目次が古い、または未挿入でした。 |
| 2 | 入出力、マーカー、引数などのエラー。理由は標準エラーに出力します。 |

## テスト

```sh
python3 -m unittest discover -s tools/markdown-toc/tests -v
```
