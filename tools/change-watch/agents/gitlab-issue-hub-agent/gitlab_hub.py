"""gitlab-issue-hub-agent — 決めた 1 つの GitLab の issue（ハブ）のための change-watch のプラグインと、コメントの本文を作るコマンド。

change-watch のプラグイン（register(api)）として、フィルタとコマンドを足す:

    change-watch --plugin gitlab_hub ...        （入れてあれば名前で。ほかの場所ならこのファイルのパス）
        フィルタ gitlab_issue_api      {{ env.GITLAB_HUB_ISSUE_URL | gitlab_issue_api }}
                                       issue の URL（https://gitlab.example.com/group/project/-/issues/42）を、その issue の
                                       API の URL（https://gitlab.example.com/api/v4/projects/group%2Fproject/issues/42）にする。
                                       設定の取得元と、ワークフローの call の URL に書く（ハブは issue の URL 1 つで決める）

    change-watch --plugin <このファイル> note --text "..."               本文をそのまま
    change-watch --plugin <このファイル> note --text-file F              ファイルの中身
    change-watch --plugin <このファイル> note -- <コマンド> [引数...]     コマンドの標準出力
        issue に足すコメントの本文を {"body": "..."} の JSON で出す（change-watch call の --json に渡す）。
        本文が空なら何も出さない。コマンドが失敗したら何も出さず、その終了コードで終わる。

    change-watch --plugin <このファイル> fields title="【完了】${CHANGE.record.title}" add_labels=完了
        issue の更新の JSON（{"title": "...", "add_labels": "..."}）を出す（change-watch call PUT の --json に渡す）。
        値はすべて文字列。Dagu の引数に、引用符を含む題などをそのまま渡せる。

    change-watch --plugin <このファイル> description --text "..." | --text-file F | -- <コマンド> [引数...]
        issue の説明を書き換える JSON（{"description": "..."}）を出す（change-watch call PUT の --json に渡す）。
        本文の渡し方と、空・失敗のときの扱いは note と同じ（ファイルやコマンドの出力から、何行もある説明を渡せる）。

    change-watch --plugin <このファイル> api-url [--url=<issue の URL>]
        issue の API の URL を出す（change-watch call の URL に渡す）。ハブでない issue を、ワークフローの引数で指して変えるときに使う。
        --url が無いか空なら、ハブの issue（環境変数 GITLAB_HUB_ISSUE_URL）。

    これらのコマンドは設定を読まない（--config・--connections は要らない）。python3 <このファイル> note ... と直接呼んでも同じ。

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


# ---------------------------------------------------------------------------
# コメントの本文・説明・更新の項目（Dagu のワークフローから、change-watch のコマンドとして呼ぶ）
# ---------------------------------------------------------------------------

def disarm(text: str) -> str:
    """行頭の / を \\/ にして、GitLab のクイックアクション（/close・/assign など）として効かないようにする。
    Markdown では \\/ は / と表示される。"""
    return _QUICK_ACTION.sub(lambda m: m.group(1) + "\\/", text)


def note_json(text: str, field: str = "body") -> str:
    return json.dumps({field: disarm(text.strip())})         # ASCII だけで出す（Windows のコンソールの文字コードに依らない）


def _run(command: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
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
        except (OSError, UnicodeError) as e:
            print(f"本文のファイルを読めません: {e}", file=sys.stderr)
            return USAGE
    else:
        text = a.text
    if text.strip():
        print(note_json(text, "description" if a.cmd == "description" else "body"))
    return OK                               # 空なら何も出さない（ワークフローは書き込みのステップを飛ばす）


def cmd_api_url(a) -> int:
    url = (a.url or "").strip() or os.environ.get("GITLAB_HUB_ISSUE_URL", "")
    try:
        print(gitlab_issue_api(url))
    except ValueError as e:
        print(e, file=sys.stderr)
        return USAGE
    return OK


def cmd_fields(a) -> int:
    out: dict[str, str] = {}
    for item in a.pairs:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            print(f"項目は 名前=値 で書いてください: {item!r}", file=sys.stderr)
            return USAGE
        out[key.strip()] = disarm(value) if key.strip() == "description" else value
    if not out:
        print("項目がありません（title=... のように渡してください）", file=sys.stderr)
        return USAGE
    print(json.dumps(out))
    return OK


_NOTE = "コメントの本文を {\"body\": ...} で出す（change-watch call の --json に渡す）"
_DESCRIPTION = "issue の説明を {\"description\": ...} で出す（change-watch call PUT の --json に渡す）"
_FIELDS = "issue の更新の JSON を 名前=値 から出す（change-watch call PUT の --json に渡す）"
_API_URL = "issue の API の URL を出す（change-watch call の URL に渡す）"


def _text_args(p, cmd: str) -> None:
    p.set_defaults(cmd=cmd)
    p.add_argument("--text", default=None, help="本文（- で始まる文も渡せるよう、--text=... の形で書く）")
    p.add_argument("--text-file", help="本文のファイル（UTF-8）")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- のあとに書いたコマンドの標準出力を本文にする")


def _fields_args(p) -> None:
    p.add_argument("pairs", nargs="+", metavar="名前=値", help="title=...・add_labels=...・description=...・state_event=close など")


def _api_url_args(p) -> None:
    p.add_argument("--url", default="", help="issue の URL（無いか空なら、環境変数 GITLAB_HUB_ISSUE_URL のハブの issue）")


def register(api) -> None:
    """フィルタと、ワークフローが呼ぶコマンド（change-watch --plugin <このファイル> note ... など。設定は読まない）。"""
    api.add_filter("gitlab_issue_api", gitlab_issue_api)
    _text_args(api.add_command("note", lambda _cfg, _store, a: cmd_note(a), _NOTE, needs_config=False), "note")
    _text_args(api.add_command("description", lambda _cfg, _store, a: cmd_note(a), _DESCRIPTION, needs_config=False), "description")
    _fields_args(api.add_command("fields", lambda _cfg, _store, a: cmd_fields(a), _FIELDS, needs_config=False))
    _api_url_args(api.add_command("api-url", lambda _cfg, _store, a: cmd_api_url(a), _API_URL, needs_config=False))


def main(argv: list[str] | None = None) -> int:
    """python3 <このファイル> ... で直接呼ぶ口（change-watch を通すのと同じコマンド）。"""
    p = argparse.ArgumentParser(prog="gitlab_hub.py", description="issue に書くもの（コメントの本文・説明・更新の項目）を JSON で出す")
    sub = p.add_subparsers(dest="cmd", required=True)
    _text_args(sub.add_parser("note", help=_NOTE), "note")
    _text_args(sub.add_parser("description", help=_DESCRIPTION), "description")
    _fields_args(sub.add_parser("fields", help=_FIELDS))
    _api_url_args(sub.add_parser("api-url", help=_API_URL))
    a = p.parse_args(argv)
    return {"note": cmd_note, "description": cmd_note, "api-url": cmd_api_url, "fields": cmd_fields}[a.cmd](a)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # 案内の文が、コンソールの文字コードで書けなくても止まらない
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    sys.exit(main())
