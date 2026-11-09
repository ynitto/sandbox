"""gitlab-issue-hub-agent — 決めた 1 つの GitLab の issue（ハブ）に書くコメントの本文を作るコマンド。

Dagu のワークフローから呼び、標準出力に GitLab の「コメントを足す」要求の本文（JSON の {"body": ...}）を出す。
それを change-watch call の --json に渡して書き込む（書き込み・認証・要求の間隔は change-watch が受け持つ）:

    python3 <このファイル> note --title "夜間バッチ" --text "終わりました"
        {"body": "【通知】夜間バッチ\\n\\n終わりました"}                    通知（ほかのワークフローから知らせる）

    python3 <このファイル> reply "${CHANGE}" -- python3 /path/to/on_request.py
        依頼のコメント（change-watch get の出した変化）を渡してコマンドを動かし、その標準出力を返信の本文にする:
        {"body": "【返信】<依頼の 1 行目>\\n\\n<標準出力>"}
        コマンドが失敗したら「処理できませんでした」の返信を出し、コマンドの終了コードで終わる（Dagu の履歴には失敗として残る）

コマンドには、依頼のコメントを次の形で渡す:
    標準入力          変化の JSON（{"key", "kind", "record", ...}。record が GitLab のコメント）
    HUB_SUBJECT       1 行目から【依頼】を除いたもの（メールの件名を置く想定）
    HUB_TEXT          2 行目から後（メールの本文を置く想定）
    HUB_AUTHOR        書いた人のユーザー名
    HUB_NOTE_ID       コメントの ID

ここで受け持つのは、本文の形を決まったものにすること（メールの件名・本文で Power Automate が見分けられるように、1 行目に
【通知】・【返信】の印を付ける）と、書き込む文に混ざった GitLab のクイックアクション（行頭の /close など）を効かなくすることだけ。
どのコメントを依頼として扱うか（1 行目の【依頼】）は Dagu のワークフローの preconditions に書く。
標準ライブラリだけで動く。設計は docs/designs/gitlab-issue-hub-agent-design.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any

NOTIFY = "【通知】"
REPLY = "【返信】"
REQUEST = "【依頼】"          # ワークフローの preconditions（re:^【依頼】）と同じにする

OK = 0
USAGE = 2
NOT_FOUND = 127

_QUICK_ACTION = re.compile(r"(?m)^(\s*)/")


def disarm(text: str) -> str:
    """行頭の / を \\/ にして、GitLab のクイックアクション（/close・/assign など）として効かないようにする。
    Markdown では \\/ は / と表示される。"""
    return _QUICK_ACTION.sub(lambda m: m.group(1) + "\\/", text)


def split_request(body: Any) -> tuple[str, str]:
    """依頼のコメントを (1 行目から【依頼】を除いたもの, 2 行目から後) に分ける。"""
    lines = str(body or "").strip().splitlines()
    if not lines:
        return "", ""
    head = lines[0].strip()
    if head.startswith(REQUEST):
        head = head[len(REQUEST):].strip()
    return head, "\n".join(lines[1:]).strip()


def _record(change: Any) -> dict:
    record = change.get("record") if isinstance(change, dict) else None
    return record if isinstance(record, dict) else {}


def compose(head: str, text: str) -> str:
    text = str(text or "").strip()
    return disarm(head if not text else f"{head}\n\n{text}")


def note_json(body: str) -> str:
    return json.dumps({"body": body}, ensure_ascii=False)


def cmd_note(a) -> int:
    if a.text is not None and a.text_file:
        print("--text と --text-file はどちらか一方にしてください", file=sys.stderr)
        return USAGE
    text = a.text or ""
    if a.text_file:
        try:
            with open(a.text_file, encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as e:
            print(f"本文のファイルを読めません: {e}", file=sys.stderr)
            return USAGE
    title = str(a.title or "").strip().splitlines()
    if not title and not text.strip():
        print("書くことがありません（--title か --text を渡してください）", file=sys.stderr)
        return USAGE
    print(note_json(compose(NOTIFY + (title[0].strip() if title else ""), text)))
    return OK


def cmd_reply(a) -> int:
    try:
        change = json.loads(a.change)
    except ValueError as e:
        print(f"依頼のコメント（変化の JSON）を読めません: {e}", file=sys.stderr)
        return USAGE
    command = list(a.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("動かすコマンドがありません（reply '<変化>' -- <コマンド> ...）", file=sys.stderr)
        return USAGE
    record = _record(change)
    subject, text = split_request(record.get("body"))
    env = dict(os.environ, HUB_SUBJECT=subject, HUB_TEXT=text,
               HUB_AUTHOR=str((record.get("author") or {}).get("username") or ""),
               HUB_NOTE_ID=str(record.get("id") or change.get("key") or ""), PYTHONUTF8="1")
    head = REPLY + subject
    try:
        p = subprocess.run(command, input=json.dumps(change, ensure_ascii=False), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
    except OSError as e:
        print(f"コマンドを動かせません: {e}", file=sys.stderr)
        print(note_json(compose(head, "処理できませんでした（コマンドを動かせません）。")))
        return NOT_FOUND
    if p.stderr:
        sys.stderr.write(p.stderr)          # Dagu のログに残す（コメントには書かない）
    if p.returncode != 0:
        print(f"コマンドが終了コード {p.returncode} で終わりました", file=sys.stderr)
        print(note_json(compose(head, f"処理できませんでした（終了コード {p.returncode}）。")))
        return p.returncode
    if p.stdout.strip():
        print(note_json(compose(head, p.stdout)))
    return OK                               # 何も出さなければ返信しない


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gitlab_hub.py", description="ハブの issue に書くコメントの本文（JSON）を出す")
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("note", help="通知のコメントの本文を {\"body\": ...} で出す（change-watch call の --json に渡す）")
    n.add_argument("--title", default="", help="1 行目（【通知】のあと）。メールで見分けるのに使う")
    n.add_argument("--text", default=None, help="本文（- で始まる文も渡せるよう、--text=... の形で書く）")
    n.add_argument("--text-file", help="本文のファイル（UTF-8）")
    r = sub.add_parser("reply", help="依頼のコメントを渡してコマンドを動かし、その出力を返信の本文にする")
    r.add_argument("change", help="依頼のコメント（change-watch get の出した変化の JSON）")
    r.add_argument("command", nargs=argparse.REMAINDER, help="-- のあとに動かすコマンド")
    a = p.parse_args(argv)
    return cmd_note(a) if a.cmd == "note" else cmd_reply(a)


if __name__ == "__main__":
    sys.exit(main())
