"""dagu-issue-loop — GitLab の issue のための api-watch のプラグイン（`api-watch --plugin <このファイル>` で読み込む）。

issue の取得・変化の検知・実行待ち・再試行・実行の前後の取り直し・1 セッションの排他は api-watch が、エージェント CLI の
起動と停止は Dagu（harness.run）が受け持つ。ここで足すのは GitLab の issue に固有の 3 つだけ:

    フィルタ gitlab_issue_changes   {{ event | gitlab_issue_changes }}  前回処理し終えたときからの変化の箇条書き
    フィルタ gitlab_issue_kind      {{ event | gitlab_issue_kind }}     新規 / 更新
    チェック gitlab.others_acted    実行を始めてから、自分以外の人が issue にコメント・操作したか
                                    （受け渡し先の handled_before_if に書く。真なら、処理し終えたのは実行前の状態まで）

標準ライブラリだけで動く。api-watch の本体の内部には触らず、プラグインの口（register(api)）と、チェックに渡る
CheckContext（GitLab への問い合わせは取得元の接続で、要求の間隔・失敗の待ちを分け合う）だけを使う。
設計は docs/designs/dagu-issue-loop-design.md。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

# 変化として扱う項目（api-watch の設定のルールの watch と同じにする）
WATCHED = ("title", "state", "labels", "assignees", "milestone", "due_date", "user_notes_count", "description")


# ---------------------------------------------------------------------------
# 変化の箇条書き
# ---------------------------------------------------------------------------

def snapshot(issue: dict[str, Any]) -> dict[str, Any]:
    desc = str(issue.get("description") or "")
    milestone = issue.get("milestone")
    return {
        "title": str(issue.get("title") or ""),
        "state": str(issue.get("state") or ""),
        "labels": sorted(str(x) for x in (issue.get("labels") or [])),
        "assignees": sorted(str(a.get("username")) for a in (issue.get("assignees") or []) if isinstance(a, dict)),
        "milestone": str(milestone.get("title")) if isinstance(milestone, dict) else "",
        "due_date": str(issue.get("due_date") or ""),
        "user_notes_count": int(issue.get("user_notes_count") or 0),
        "description_sha": hashlib.sha256(desc.encode()).hexdigest()[:16],
    }


def describe_changes(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[str]:
    """箇条書きの行。prev が無ければ新しい issue。"""
    if not prev:
        return ["- 新しい issue"]
    lines: list[str] = []
    if prev.get("state") != cur["state"]:
        lines.append(f"- 状態: {prev.get('state')} → {cur['state']}")
    if prev.get("title") != cur["title"]:
        lines.append(f"- タイトル: {prev.get('title')} → {cur['title']}")
    added = sorted(set(cur["labels"]) - set(prev.get("labels") or []))
    removed = sorted(set(prev.get("labels") or []) - set(cur["labels"]))
    if added:
        lines.append("- ラベル追加: " + ", ".join(added))
    if removed:
        lines.append("- ラベル削除: " + ", ".join(removed))
    if prev.get("assignees") != cur["assignees"]:
        lines.append("- 担当: " + (", ".join(cur["assignees"]) or "（なし）"))
    if prev.get("milestone") != cur["milestone"]:
        lines.append(f"- マイルストーン: {cur['milestone'] or '（なし）'}")
    if prev.get("due_date") != cur["due_date"]:
        lines.append(f"- 期日: {cur['due_date'] or '（なし）'}")
    diff_notes = cur["user_notes_count"] - int(prev.get("user_notes_count") or 0)
    if diff_notes > 0:
        lines.append(f"- コメント +{diff_notes}")
    if prev.get("description_sha") != cur["description_sha"]:
        lines.append("- 本文が更新された")
    return lines or ["- 監視している項目の変化なし"]


def issue_before(before: Any, current: dict[str, Any]) -> dict[str, Any]:
    """api-watch の監視する値（ルールの watch の項目だけ）を、今の issue に重ねて変化前の issue にする。
    監視していない項目は今と同じとみなす（変化の箇条書きに余計な行を出さない）。"""
    out = dict(current)
    if not isinstance(before, dict):
        return out
    for name in ("title", "state", "labels", "due_date", "user_notes_count", "description"):
        if name in before:
            out[name] = before[name]
    if "assignees[*].username" in before:
        out["assignees"] = [{"username": u} for u in (before["assignees[*].username"] or [])]
    elif "assignees" in before:
        out["assignees"] = before["assignees"]
    if "milestone.title" in before:
        out["milestone"] = {"title": before["milestone.title"]} if before["milestone.title"] else None
    elif "milestone" in before:
        out["milestone"] = before["milestone"]
    return out


def _base(event: dict) -> dict[str, Any] | None:
    """比べる元の issue。前回処理し終えたとき（refresh の last_handled）、無ければイベントの before。新しい issue なら None。"""
    issue = event.get("record") or {}
    last = (event.get("refreshed") or {}).get("last_handled")
    if last is not None:
        return snapshot(issue_before(last, issue))
    if "appeared" in (event.get("kinds") or []) or event.get("before") is None:
        return None
    return snapshot(issue_before(event.get("before"), issue))


def gitlab_issue_changes(event: Any, _arg: Any = None) -> str:
    if not isinstance(event, dict) or not isinstance(event.get("record"), dict):
        raise ValueError("gitlab_issue_changes にはイベント（{{ event | gitlab_issue_changes }}）を渡してください")
    return "\n".join(describe_changes(_base(event), snapshot(event["record"])))


def gitlab_issue_kind(event: Any, _arg: Any = None) -> str:
    if not isinstance(event, dict):
        return "更新"
    return "新規" if _base(event) is None else "更新"


# ---------------------------------------------------------------------------
# 実行中に自分以外が動いたか
# ---------------------------------------------------------------------------

def _ts(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def others_acted(ctx) -> bool:
    """実行を始めてから、自分（トークンの持ち主か options.self_username）以外の人が issue の notes を書いたか。

    notes にはシステムノート（ラベル変更など）も含まれ、作者で判定する。自分が分からないときは、自分の書き込みと
    区別できないので偽（同じ issue を流し続けない）。GitLab に届かなければ例外のまま返し、api-watch が
    handled_before_if を真とみなす（取りこぼさない側）。

    options:
        self_username  自分のユーザー名（無ければ GET {url}/api/v4/user で引く）
        url            GitLab の URL（無ければ接続設定の url）
    """
    url = str(ctx.options.get("url") or ctx.conn.get("url") or "").rstrip("/")
    if not url:
        raise ValueError("GitLab の URL がありません（接続設定の url か、options.url）")
    me = str(ctx.options.get("self_username") or "")
    if not me:
        user = ctx.get_json(f"{url}/api/v4/user")
        me = str((user or {}).get("username") or "")
        if not me:
            ctx.log("自分のユーザー名が分からないので、実行中の他人の動きは拾えません（options.self_username で指定できます）")
            return False
    issue = ctx.event.get("record") or {}
    project = issue.get("project_id")
    iid = issue.get("iid")
    if project is None or iid is None:
        raise ValueError("イベントのレコードに project_id と iid がありません")
    notes = ctx.get_json(f"{url}/api/v4/projects/{project}/issues/{iid}/notes",
                         query={"order_by": "created_at", "sort": "desc", "per_page": "50"}) or []
    since = float(ctx.delivery.get("started_at") or 0)
    for note in notes:
        if not isinstance(note, dict):
            continue
        if _ts(note.get("created_at")) < since:
            break
        author = (note.get("author") or {}).get("username")
        if author and author != me:
            ctx.log(f"#{iid} 実行中に {author} が書き込んでいました")
            return True
    return False


def register(api) -> None:
    api.add_filter("gitlab_issue_changes", gitlab_issue_changes)
    api.add_filter("gitlab_issue_kind", gitlab_issue_kind)
    api.add_check("gitlab.others_acted", others_acted)
