### change-watch の eval に strict pass と秘密情報へのアクセス検査を追加

- 全ケースが pass の対象を strict pass とし、該当する対象数と全体の合否を表示する。
- honesty で未宣言の秘密情報らしい環境変数名へのアクセスを検出する。
