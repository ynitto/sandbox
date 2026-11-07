"""redmine-ticket-agent — Redmine のチケットのための change-watch のプラグインと、チケットを完了にするコマンド。

1 つのファイルを 2 つの使い方で使う:

    change-watch --plugin <このファイル> ...      プラグインとして読み込む（register(api)）
        チェック redmine.not_mine            実行の直前に取り直したチケットの担当が自分（API キーの持ち主）でなければ真。
                                             受け渡し先の skip_if に書くと、待つ間に担当が替わったチケットを完了にしない

    python3 <このファイル> close --status 完了 --note "..."
                                             change-watch の command の受け渡し先から呼ぶ。CHANGEWATCH_EVENT_FILE のチケットに
                                             コメントを書き、ステータスを変える（1 回の PUT）

取得・変化の検知・実行待ち・再試行・実行の前後の取り直しは change-watch が受け持つ。どのチケットを対象にするか
（自分が担当・タイトルや説明の文字列）は change-watch の設定（取得元の query とルールの filter）に書く。
close は Redmine の URL と API キーを環境変数 REDMINE_URL / REDMINE_API_KEY から読む（Dagu ならワークフローの dotenv に書いた .env）。
標準ライブラリだけで動く。設計は docs/designs/redmine-ticket-agent-design.md。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# 終了コード（change-watch の work は 0 を完了、それ以外を失敗として、あとでやり直す）
OK = 0
FAILED = 1
USAGE = 2


# ---------------------------------------------------------------------------
# プラグイン（change-watch --plugin で読み込む）
# ---------------------------------------------------------------------------

def _base_url(ctx) -> str:
    url = str(ctx.options.get("url") or ctx.conn.get("url") or "").rstrip("/")
    if not url:
        raise ValueError("Redmine の URL がありません（接続設定の url か、options.url）")
    return url


def not_mine(ctx) -> bool:
    """取り直したチケットの担当（assigned_to.id）が自分でなければ真（skip_if に書く）。

    自分は options.user_id、無ければ GET {url}/users/current.json（API キーの持ち主）。担当がグループのときも、
    自分の ID と違うので真（完了にしない）。Redmine に届かなければ例外のまま返し、change-watch が skip_if を
    偽とみなす（取得の query で自分の担当に絞っているので、実行する側に倒してよい）。

    options:
        user_id  自分のユーザー ID（無ければ /users/current.json で引く）
        url      Redmine の URL（無ければ接続設定の url）
    """
    me = ctx.options.get("user_id")
    if me in (None, ""):
        user = (ctx.get_json(f"{_base_url(ctx)}/users/current.json") or {}).get("user") or {}
        me = user.get("id")
        if me is None:
            raise ValueError("自分のユーザー ID が分かりません（options.user_id で指定できます）")
    issue = ctx.event.get("record") or {}
    assignee = (issue.get("assigned_to") or {}).get("id")
    if str(assignee) != str(me):
        ctx.log(f"#{issue.get('id')} の担当が自分ではない（{(issue.get('assigned_to') or {}).get('name') or 'なし'}）ので、"
                "完了にしません")
        return True
    return False


def register(api) -> None:
    api.add_check("redmine.not_mine", not_mine)


# ---------------------------------------------------------------------------
# close（change-watch の command の受け渡し先から呼ぶ）
# ---------------------------------------------------------------------------

class RedmineError(Exception):
    pass


class Redmine:
    def __init__(self, url: str, key: str, timeout: float = 30):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self._statuses: list[dict] | None = None

    def call(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"X-Redmine-API-Key": self.key, "Accept": "application/json",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                errors = json.loads(e.read().decode("utf-8")).get("errors")
                detail = "（" + "・".join(str(x) for x in errors) + "）" if errors else ""
            except (ValueError, AttributeError):
                pass
            raise RedmineError(f"{method} {path} が {e.code} で失敗しました{detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise RedmineError(f"{method} {path} が届きません: {getattr(e, 'reason', e)}") from None
        return json.loads(raw.decode("utf-8")) if raw.strip() else None

    def issue(self, issue_id: Any, include: str | None = None) -> dict:
        body = self.call("GET", f"/issues/{issue_id}.json" + (f"?include={include}" if include else ""))
        return (body or {}).get("issue") or {}

    def statuses(self) -> list[dict]:
        """/issue_statuses.json のステータスの一覧（1 回だけ引く）。"""
        if self._statuses is None:
            self._statuses = (self.call("GET", "/issue_statuses.json") or {}).get("issue_statuses") or []
        return self._statuses

    def is_closed(self, status: dict | None) -> bool:
        """チケットのステータスが完了扱いか。Redmine 5.1 より前はチケットに is_closed が無いので、一覧で引く。"""
        status = status or {}
        if "is_closed" in status:
            return bool(status["is_closed"])
        return any(s.get("id") == status.get("id") and s.get("is_closed") for s in self.statuses())

    def status_id(self, status: str) -> int:
        """数ならそのまま ID、名前なら /issue_statuses.json で引く。"""
        if str(status).isdigit():
            return int(status)
        statuses = self.statuses()
        for s in statuses:
            if str(s.get("name")) == str(status):
                return int(s["id"])
        names = "・".join(str(s.get("name")) for s in statuses)
        raise RedmineError(f"ステータス「{status}」が Redmine にありません（あるもの: {names}）")


def close(redmine: Redmine, issue: dict, status: str, note: str, log=print) -> int:
    """チケットにコメントを書き、ステータスを status にする。既にそのステータスか、ほかの完了扱いのステータスなら何もしない。

    完了扱いのチケットに触らないのは、1 つのチケットが複数のルール（条件とコメントの対）に当たったとき、上のルールが
    完了にしたあとで下のルールがコメントを重ねたりステータスを変えたりしないため。
    """
    iid = issue.get("id")
    if iid is None:
        raise RedmineError("イベントのチケットに id がありません")
    target = redmine.status_id(status)
    if (issue.get("status") or {}).get("id") == target:
        log(f"#{iid} は既に「{issue['status'].get('name')}」なので、何もしません")
        return OK
    # 自分がこのチケットで選べるステータスか（Redmine 5.0 以降は allowed_statuses を返す）。選べないステータスは
    # PUT しても黙って無視され、コメントだけが残るので、書く前に確かめる
    current = redmine.issue(iid, include="allowed_statuses,journals")
    if (current.get("status") or {}).get("id") == target or redmine.is_closed(current.get("status")):
        log(f"#{iid} は既に「{(current.get('status') or {}).get('name')}」なので、何もしません")
        return OK
    allowed = current.get("allowed_statuses")
    if isinstance(allowed, list) and target not in [s.get("id") for s in allowed]:
        names = "・".join(str(s.get("name")) for s in allowed) or "なし"
        raise RedmineError(f"#{iid} は「{status}」に変えられません（ワークフローで選べるもの: {names}）")
    change: dict[str, Any] = {"status_id": target}
    # 前の試行でコメントだけが残っていたら（ステータスが無視された）、同じコメントを重ねない
    if any(str((j or {}).get("notes") or "").strip() == note for j in current.get("journals") or []):
        log(f"#{iid} には同じコメントが既にあるので、ステータスだけを変えます")
    else:
        change["notes"] = note
    redmine.call("PUT", f"/issues/{iid}.json", {"issue": change})
    after = redmine.issue(iid)
    if (after.get("status") or {}).get("id") != target:
        raise RedmineError(f"#{iid} にコメントしましたが、ステータスが「{status}」になりませんでした"
                           f"（今は「{(after.get('status') or {}).get('name')}」。権限とワークフローを確かめてください）")
    log(f"#{iid}「{issue.get('subject')}」にコメントし、「{status}」にしました")
    return OK


def _read_note(args) -> str:
    if args.note_file:
        with open(args.note_file, encoding="utf-8") as f:
            return f.read().strip()
    return str(args.note or "").strip()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")     # Windows の cp932 / cp1252 でも日本語のまま出す（change-watch は UTF-8 で読む）
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="redmine_issue.py", description="Redmine のチケットにコメントしてステータスを変える")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("close", help="CHANGEWATCH_EVENT_FILE のチケットにコメントし、ステータスを変える")
    c.add_argument("--status", required=True, help="変えるステータスの名前か ID（例: 完了・5）")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--note", help="コメントする文")
    g.add_argument("--note-file", help="コメントする文のファイル（UTF-8）")
    c.add_argument("--event-file", default=os.environ.get("CHANGEWATCH_EVENT_FILE"),
                   help="イベントの JSON（既定は環境変数 CHANGEWATCH_EVENT_FILE）")
    args = p.parse_args(argv)

    url, key = os.environ.get("REDMINE_URL", ""), os.environ.get("REDMINE_API_KEY", "")
    if not url or not key:
        print("環境変数 REDMINE_URL と REDMINE_API_KEY を設定してください", file=sys.stderr)
        return USAGE
    if not args.event_file:
        print("イベントの JSON がありません（change-watch から呼ぶか、--event-file を付けてください）", file=sys.stderr)
        return USAGE
    try:
        note = _read_note(args)
        if not note:
            print("コメントする文が空です", file=sys.stderr)
            return USAGE
        with open(args.event_file, encoding="utf-8") as f:
            event = json.load(f)
        return close(Redmine(url, key), event.get("record") or {}, args.status, note,
                     log=lambda m: print(m, file=sys.stderr))
    except (OSError, ValueError, RedmineError) as e:
        print(str(e), file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
