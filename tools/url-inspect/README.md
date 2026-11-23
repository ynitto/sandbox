# url-inspect

URL の構成要素とクエリパラメータを表示し、クエリを編集して URL を再構築する CLI です。Python 3.9 以降の標準ライブラリだけで動作します。

## 使い方

```sh
python3 tools/url-inspect/url_inspect.py 'https://example.com/search?q=hello+world&flag'
python3 tools/url-inspect/url_inspect.py --json --drop flag --set 'q=日本語' --add 'tag=新着' 'https://example.com/search?q=old&flag'
python3 tools/url-inspect/url_inspect.py --fix-scheme 'example.com:8080/path'
python3 tools/url-inspect/url_inspect.py --decode 'hello%20world'
python3 tools/url-inspect/url_inspect.py --encode 'a/b c' --safe '/'
```

## オプション

| オプション | 説明 |
| --- | --- |
| `--json` | `components` オブジェクトと `params` 配列を JSON で出力します。編集時は `rebuilt` も含みます。 |
| `--drop key` | 同じキーのパラメータをすべて削除します。 |
| `--set key=value` | 同じキーのパラメータを最初の出現位置で 1 件にまとめ、新規キーは末尾に追加します。 |
| `--add key=value` | パラメータを末尾に追加します。 |
| `--fix-scheme` | スキームが省略されていれば HTTPS を補います。 |
| `--decode 文字列` | 文字列をパーセントデコードします。 |
| `--encode 文字列` | 文字列をパーセントエンコードします。 |
| `--safe 文字列` | `--encode` でエンコード対象から除外する文字を追加します。 |
| `-h`, `--help` | ヘルプを表示します。 |

## 仕様

- 編集オプションは複数回指定でき、コマンドライン上の並びにかかわらず **drop → set → add** の順で適用します。`--set` と `--add` はそれぞれ指定順に処理します。編集オプションのキーはクエリの表示と同じフォーム形式でデコードして突き合わせます。`--set` と `--add` の値は入力された文字列として扱います。
- 分解表示では元の URL を解析し、ホスト名を小文字で表示します。`rebuilt` は編集後の URL で、`--fix-scheme` による補完後、クエリパラメータ以外はホスト名の大文字・小文字を含め元の表記を保持します。
- クエリは `&` で区切り、繰り返しキーも出現順に表示します。`?flag` は値なし（JSON では `null`）、`?a=` は空文字です。再構築後もこの区別を保持します。
- クエリのキーと値は UTF-8 でデコードし、`+` を空白として扱います。単独の `--decode` では `+` はそのままです。不正なエスケープや UTF-8 がある場合は該当するキー、値、または文字列をそのまま表示し、標準エラーに警告します。
- `--encode` の既定の safe は空文字列です。`/` も `%2F` に変換します。文字列モードは URL 引数や URL 用オプションと併用できません。
- ホストが必要なスキームは `http`、`https`、`ws`、`wss`、`ftp`、`ssh` です。`http`、`https`、`ws`、`wss`、`ftp` はポート未指定時に既定値を表示し、JSON では `port_is_default` で区別します。既定値がなくポートも未指定の場合、テキスト出力では `port` 行を省略し、JSON では `port` が `null` になります。IPv6 アドレスは角括弧で囲んで指定します。

## 既知の制限

- URL の構文を解析します。DNS 解決や接続確認は行いません。userinfo、パス、フラグメントのパーセントエスケープは元の表記で表示します。
- 再構築時はクエリをフォーム形式でエンコードし直すため、元のエスケープ表記（例 `%20`）は変わることがあります。連続する `&` などによる空の区切りは無視します。

## 終了コード

| コード | 意味 |
| --- | --- |
| 0 | 正常終了 |
| 2 | URL の解釈に失敗したか、引数が無効です。理由は標準エラーに出力します。 |
