"""api-watch: Dagu から動かす、汎用の API ポーリング・変更検知。

モジュールの分担:
    util        共通の小物（正規化 JSON・ハッシュ・圧縮・プロセスの生存確認・秘密の伏せ字）
    jpath       レコードの中の値を指すパスと、条件式の評価
    template    設定の文字列に埋める {{ ... }} の展開
    connections connections.yaml（スキル共通の接続設定）の読み込み
    config      設定ファイルの読み込みと検査
    store       状態（SQLite）
    httpclient  1 回の HTTP 要求（再試行・レート制限・条件付き要求）
    fetch       取得元ごとの取得（ページング・増分・途中からの再開）
    detect      ルールごとの判定（変更検知・発火条件）
    deliver     受け渡し（再送・順序・完了の記録）と、その場で実行する受け渡し先の実行（work）
    cli         コマンド行
"""

VERSION = "0.2.0"
