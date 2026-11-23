### tools/jwt-inspect を追加

- JWT の Header、Payload、時刻クレーム、署名の有無とバイト数を表示する Python CLI を追加。
- `--secret` を指定した場合に HS256 の署名を検証する。
