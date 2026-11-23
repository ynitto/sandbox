# jwt-inspect

JWT の Header と Payload を整形して表示し、時刻クレームと署名の有無・バイト数を確認する CLI です。Python 3.9 以降の標準ライブラリだけで動作します。既定では署名を検証しません。`--secret` を指定した場合に限り、HS256 の署名を検証します。

## 使い方

リポジトリのルートで実行します。`$TOKEN` には確認する JWT を入れてください。

```sh
# 位置引数
python3 tools/jwt-inspect/jwt_inspect.py "$TOKEN"

# 標準入力
printf '%s\n' "$TOKEN" | python3 tools/jwt-inspect/jwt_inspect.py

# ファイル
python3 tools/jwt-inspect/jwt_inspect.py --file token.txt

# HS256 の署名検証
python3 tools/jwt-inspect/jwt_inspect.py --secret "$JWT_SECRET" "$TOKEN"

# タイムゾーン指定
python3 tools/jwt-inspect/jwt_inspect.py --tz Asia/Tokyo "$TOKEN"
```

位置引数、`--file`、標準入力の順で入力を選び、トークンに含まれる空白と改行を除去します。`exp`、`nbf`、`iat`、`auth_time` は既定で UTC の ISO 8601 日時として表示します。`--tz` には実行環境で利用できる IANA タイムゾーン名を指定できます。期限切れと有効開始前の警告は標準出力に表示されます。

## 終了コード

| コード | 意味 |
| --- | --- |
| 0 | デコード成功。`--secret` 指定時は署名検証も成功 |
| 1 | 実行時エラー（ファイル読み込み失敗やタイムゾーン指定エラーなど） |
| 2 | JWT の形式が不正、または CLI の引数が不正（理由は標準エラーに表示） |
| 3 | 署名が一致しない、または `alg` が HS256 以外で検証できない |

時刻クレームの警告は終了コードに影響しません。
