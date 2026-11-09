"""ハブの issue に付いたコメントで動かすコマンドの例（ワークフローの request に書いたもの。/home/you/work/hub/on_request.py に置いて直す）。

引数に、受け取ったコメントの変化（change-watch get の出した JSON。record が GitLab のコメント）が届く。
標準出力に書いたものが、ハブの issue に足すコメントの本文になる（何も書かなければコメントしない）。
0 以外で終わるとコメントせず、Dagu の実行履歴に失敗として残る。
"""
import json
import sys

change = json.loads(sys.argv[1])
note = change["record"]
lines = str(note.get("body") or "").strip().splitlines()
subject = lines[0].removeprefix("【依頼】").strip() if lines else ""

# ここに処理を書く。例: 件名で分けて、決まったスクリプトを動かす
print(f"【返信】{subject}\n\n{note['author']['username']} さんの依頼を受け付けました（{len(lines) - 1} 行）。")
