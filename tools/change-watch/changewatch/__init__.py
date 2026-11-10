"""change-watch: Dagu のワークフローから使う、API（とフォルダ）へのアクセスの口。取得をまとめ、変化を返し、書き込みも通す。

モジュールの分担:
    util        共通の小物（正規化 JSON・ハッシュ・圧縮・プロセスの生存確認・秘密の伏せ字）
    jpath       レコードの中の値を指すパス
    template    設定の文字列に埋める {{ ... }} の展開
    connections connections.yaml（スキル共通の接続設定）の読み込み
    config      設定ファイルの読み込みと検査
    store       状態（SQLite）
    httpclient  1 回の HTTP 要求（再試行・レート制限・条件付き要求）
    fetch       取得元ごとの取得（ページング・増分・途中からの再開）
    folder      フォルダの取得元（フォルダの一覧をレコードにする）
    access      ワークフローから使う口（get / commit）。consumer ごとに前回 commit してからの変化を返す
    hooks       プラグインが足すテンプレートのフィルタ
    plugins     プラグイン（--plugin の .py）の読み込み
    maint       状況の確認（status）と整理（gc）
    cli         コマンド行
"""

VERSION = "0.8.0"
