"""gitlab-issue-hub-agent — 決めた 1 つの GitLab の issue（ハブ）のための change-watch のプラグインと、コメントの本文を作るコマンド。

1 つのファイルを 2 つの使い方で使う:

    change-watch --plugin <このファイル> ...      プラグインとして読み込む（register(api)）
        フィルタ gitlab_issue_api      {{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}
                                       issue の URL（https://gitlab.example.com/group/project/-/issues/42）を、その issue の
                                       API の URL（https://gitlab.example.com/api/v4/projects/group%2Fproject/issues/42）にする。
                                       設定の取得元と、ワークフローの call の URL に書く（ハブは issue の URL 1 つで決める）

    python3 <このファイル> note --text "..."               本文をそのまま
    python3 <このファイル> note --text-file F              ファイルの中身
    python3 <このファイル> note -- <コマンド> [引数...]     コマンドの標準出力
        issue に足すコメントの本文を {"body": "..."} の JSON で出す（change-watch call の --json に渡す）。
        本文が空なら何も出さない。コマンドが失敗したら何も出さず、その終了コードで終わる。

本文の中身は決めない（ワークフローとそのコマンドが決める）。ここですることは、Dagu の引数に任意の文（改行・引用符）を JSON として
渡せるようにすることと、書き込む文に混ざった GitLab のクイックアクション（行頭の /close など）を効かなくすることだけ。
どの変化をどう扱うかの条件は持たない（Dagu のワークフローの preconditions に書く）。HTTP は送らない（取得・書き込み・認証・
要求の間隔は change-watch が受け持つ）。標準ライブラリだけで動く。設計は docs/designs/gitlab-issue-hub-agent-design.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

OK = 0
USAGE = 2
NOT_FOUND = 127

_QUICK_ACTION = re.compile(r"(?m)^(\s*)/")
_ISSUE_URL = re.compile(r"^(https?://[^/?#]+)/(.+?)(?:/-)?/(?:issues|work_items)/(\d+)/?(?:[?#].*)?$")


# ---------------------------------------------------------------------------
# プラグイン（change-watch --plugin で読み込む）
# ---------------------------------------------------------------------------

def gitlab_issue_api(url, _arg=None) -> str:
    """issue の URL を、その issue の API の URL にする（プロジェクトのパスは %2F で 1 つにまとめる）。
    GitLab を https://host/gitlab/ のような下の場所に置いているときは見分けられないので、API の URL をそのまま書く
    （/api/v4/ を含む URL は手を加えずに返す）。"""
    url = str(url or "").strip()
    if "/api/v4/" in url:
        return url.rstrip("/")
    m = _ISSUE_URL.match(url)
    if not m:
        raise ValueError(f"GitLab の issue の URL ではありません: {url!r}"
                         "（https://gitlab.example.com/group/project/-/issues/42 の形で書いてください）")
    base, project, iid = m.groups()
    return f"{base}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues/{iid}"


def register(api) -> None:
    api.add_filter("gitlab_issue_api", gitlab_issue_api)


# ---------------------------------------------------------------------------
# コメントの本文（Dagu のワークフローから呼ぶ）
# ---------------------------------------------------------------------------

def disarm(text: str) -> str:
    """行頭の / を \\/ にして、GitLab のクイックアクション（/close・/assign など）として効かないようにする。
    Markdown では \\/ は / と表示される。"""
    return _QUICK_ACTION.sub(lambda m: m.group(1) + "\\/", text)


def note_json(text: str) -> str:
    return json.dumps({"body": disarm(text.strip())}, ensure_ascii=False)


def _run(command: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONUTF8="1"))
    except OSError as e:
        print(f"コマンドを動かせません: {e}", file=sys.stderr)
        return NOT_FOUND, ""
    if p.stderr:
        sys.stderr.write(p.stderr)          # Dagu のログに残す（コメントには書かない）
    if p.returncode != 0:
        print(f"コマンドが終了コード {p.returncode} で終わりました", file=sys.stderr)
    return p.returncode, p.stdout


def cmd_note(a) -> int:
    command = list(a.command or [])
    if command[:1] == ["--"]:
        command = command[1:]
    given = [x for x in (a.text is not None, bool(a.text_file), bool(command)) if x]
    if len(given) != 1:
        print("本文は --text・--text-file・-- <コマンド> のどれか 1 つで渡してください", file=sys.stderr)
        return USAGE
    if command:
        code, text = _run(command)
        if code != 0:
            return code
    elif a.text_file:
        try:
            with open(a.text_file, encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as e:
            print(f"本文のファイルを読めません: {e}", file=sys.stderr)
            return USAGE
    else:
        text = a.text
    if text.strip():
        print(note_json(text))
    return OK                               # 空なら何も出さない（ワークフローは書き込みのステップを飛ばす）


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gitlab_hub.py", description="ハブの issue に足すコメントの本文（JSON）を出す")
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("note", help="コメントの本文を {\"body\": ...} で出す（change-watch call の --json に渡す）")
    n.add_argument("--text", default=None, help="本文（- で始まる文も渡せるよう、--text=... の形で書く）")
    n.add_argument("--text-file", help="本文のファイル（UTF-8）")
    n.add_argument("command", nargs=argparse.REMAINDER, help="-- のあとに書いたコマンドの標準出力を本文にする")
    a = p.parse_args(argv)
    return cmd_note(a)


if __name__ == "__main__":
    sys.exit(main())
