"""依頼で動かすコマンドの例（ワークフローの request の最後の引数に書いたもの。/home/you/work/hub/on_request.py に置いて直す）。

依頼のコメントは、標準入力（change-watch get の出した変化の JSON）と環境変数で届く:
    HUB_SUBJECT  1 行目から【依頼】を除いたもの（メールの件名）
    HUB_TEXT     2 行目から後（メールの本文）
    HUB_AUTHOR   書いた人のユーザー名
標準出力に書いたものが返信の本文になる（何も書かなければ返信しない）。0 以外で終わると「処理できませんでした」と返信する。
"""
import json
import os
import sys

change = json.load(sys.stdin)
subject = os.environ.get("HUB_SUBJECT", "")
text = os.environ.get("HUB_TEXT", "")

# ここに依頼ごとの処理を書く。例: 件名で分けて、決まったスクリプトを動かす
print(f"「{subject}」を受け付けました（{len(text.splitlines())} 行）。")
