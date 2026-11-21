"""GitLab の判断待ちを集約し、ハブ Issue に報告する。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ISSUE_PATH = re.compile(r"(?P<project>.+?)/-/issues/(?P<iid>[1-9][0-9]*)")
class GitLabError(Exception):
    def __init__(self, method, status):
        super().__init__(f"GitLab の {method} が HTTP {status} で失敗しました")
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"環境変数 {name} を設定してください")
    return value


class GitLab:
    def __init__(self):
        self.url = env("GITLAB_URL").rstrip("/")
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or (
            parts.username is not None or parts.query or parts.fragment
        ):
            raise ValueError("GITLAB_URL に GitLab のベース URL を設定してください")
        self.token = env("GITLAB_TOKEN")
        self.opener = build_opener(NoRedirect())

    def call(self, path, *, query=None, note=None):
        url = self.url + "/api/v4/projects/" + path
        if query:
            url += "?" + urlencode(query)
        headers = {"PRIVATE-TOKEN": self.token, "Accept": "application/json"}
        data = None
        if note is not None:
            headers["Content-Type"] = "application/json"
            # gitlab-issue-hub-agent と同様、GitLab のクイックアクションを無効にする。
            body = re.sub(r"(?m)^([ \t]*)/", r"\1\\/", note)
            data = json.dumps({"body": body}).encode("utf-8")
        request = Request(url, headers=headers, data=data)
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response), response.headers
        except HTTPError as exc:
            exc.close()
            raise GitLabError(request.get_method(), exc.code) from None
        except (URLError, OSError, http.client.HTTPException):
            raise ValueError(f"GitLab の {request.get_method()} の応答を受け取れませんでした") from None

    def get(self, path):
        return self.call(path)[0]

    def pages(self, path, **query):
        page = 1
        while page <= 1000:
            items, headers = self.call(path, query={**query, "per_page": 100, "page": page})
            if not isinstance(items, list):
                raise ValueError("GitLab の一覧の応答が不正です")
            yield from items
            next_page = headers.get("X-Next-Page")
            if next_page == "" or (next_page is None and len(items) < 100):
                return
            following = page + 1 if next_page is None else int(next_page)
            if following <= page:
                raise ValueError("GitLab の次ページ番号が不正です")
            page = following
        raise ValueError("GitLab の一覧が 1000 ページを超えました")


def issue_reference(base, url):
    # 同じ GitLab インスタンス上の Issue に限定する（サブパス構成にも対応）。
    if not url.startswith(base + "/"):
        return None
    path = urlsplit(url[len(base):]).path
    match = ISSUE_PATH.fullmatch(path.strip("/"))
    if match:
        return unquote(match["project"]), int(match["iid"])
    return None


def issue_path(project, iid):
    return f"{quote(project, safe='')}/issues/{iid}"


DECISION_LABELS = "decision,needs-decision,needs,approval"
PACKET_LABELS = "decision-packet,status:review-ready"
MARKER = re.compile(r"<!-- gitlab-decision-stagnation (\d{4}-\d{2}-\d{2}) count=\d+ -->")
LEGACY_DRAFT = re.compile(r"^(?:\[Draft\]|\(Draft\)|Draft:|\[WIP\]|\(WIP\)|WIP:)", re.I)
WAITING_REVIEW = {"unreviewed", "review_started", "unapproved"}


def csv_env(name, default=""):
    return set(filter(None, (value.strip() for value in os.environ.get(name, default).split(","))))


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def optional_pages(gl, path):
    try:
        return list(gl.pages(path))
    except GitLabError as exc:
        if exc.status not in {403, 404, 405, 501}:
            raise
        return []


def label_start(gl, path, labels):
    latest = {}
    for event in optional_pages(gl, path + "/resource_label_events"):
        name = (event.get("label") or {}).get("name")
        if name in labels and event.get("created_at") and event.get("action") in {"add", "remove"}:
            order = (timestamp(event["created_at"]), event["id"])
            if name not in latest or order > latest[name][0]:
                latest[name] = (order, event["action"])

    starts = [order[0] for order, action in latest.values() if action == "add"]
    return min(starts, default=None)


def mr_wait(gl, path, mr):
    approval = mr.get("detailed_merge_status") == "not_approved"
    # detailed_merge_status can also report another blocker (e.g. CI).
    if not approval:
        try:
            state = gl.get(path + "/approvals")
        except GitLabError as exc:
            if exc.status not in {403, 404, 405, 501}:
                raise
        else:
            approval = (state["approvals_left"] > 0 if state.get("approvals_left") is not None
                        else state.get("approved") is False)

    reviews = optional_pages(gl, path + "/reviewers") if mr.get("reviewers") else []
    waiting = [review for review in reviews if review["state"] in WAITING_REVIEW]
    since = None
    if waiting and not approval and all(review.get("created_at") for review in waiting):
        since = min(timestamp(review["created_at"]) for review in waiting)
    reasons = []
    if approval:
        reasons.append("承認待ち")
    if waiting:
        reasons.append("レビュー待ち")
    return reasons, since


def collect(gl, projects, decision_labels, packet_labels, now):
    items = {}
    for project in sorted(projects):
        prefix = quote(project, safe="")
        for kind, endpoint in (("MR", "merge_requests"), ("Issue", "issues")):
            for record in gl.pages(f"{prefix}/{endpoint}", state="opened", scope="all"):
                if record["state"] != "opened":
                    continue
                if kind == "MR" and (record.get("draft") or record.get("work_in_progress")
                                     or LEGACY_DRAFT.match(record["title"])):
                    continue
                key = (kind, record["id"])
                if key in items:
                    continue

                iid = record["iid"]
                path = f"{prefix}/{endpoint}/{iid}"
                labels = set(record.get("labels") or [])
                since = None
                if kind == "MR":
                    reasons, since = mr_wait(gl, path, record)
                else:
                    reasons = []
                    if labels & decision_labels:
                        reasons.append("判断待ち")
                    if labels & packet_labels:
                        reasons.append("判断パケット関連")
                if not reasons:
                    continue

                matching = labels & (decision_labels | packet_labels)
                if matching:
                    since = label_start(gl, path, matching) or since
                since = since or timestamp(record["created_at"])
                assignees = record.get("assignees") or [record.get("assignee") or {}]
                usernames = sorted({user["username"] for user in assignees
                                    if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*",
                                                    user.get("username") or "")})
                items[key] = dict(kind=kind, iid=iid, title=record["title"], url=record["web_url"],
                                  reasons=reasons, days=max(0, (now - since).days), assignees=usernames)
    return sorted(items.values(), key=lambda item: (-item["days"], item["url"]))


def render(items, day, threshold):
    marker = f"<!-- gitlab-decision-stagnation {day} count={len(items)} -->"
    if not items:
        return f"{marker}\n判断待ちはありません\n"

    lines = [marker, f"## 判断待ちサマリ（{day}、{len(items)} 件）", ""]
    for item in items:
        symbol = "!" if item["kind"] == "MR" else "#"
        title = re.sub(r"([\\`*_\[\]<>@~|&])", r"\\\1", " ".join(item["title"].split()))
        line = (f"- [{item['kind']} {symbol}{item['iid']}](<{item['url']}>) {title}"
                f" — {item['days']} 日（{'・'.join(item['reasons'])}）")
        if item["days"] > threshold:
            mentions = " ".join("@" + user for user in item["assignees"])
            line += f" **⚠️ 要判断（滞留）** {mentions or '担当者未設定'}。判断をお願いします。"
        lines.append(line)
    return "\n".join(lines) + "\n"


def report(args):
    now = datetime.now(timezone.utc)
    projects = csv_env("GITLAB_PROJECTS")
    if not projects:
        raise ValueError("環境変数 GITLAB_PROJECTS を設定してください")
    threshold = int(os.environ.get("DECISION_STAGNATION_THRESHOLD_DAYS", "7"))
    if threshold < 0:
        raise ValueError("しきい値は 0 以上の日数を設定してください")
    items = collect(GitLab(), projects, csv_env("DECISION_STAGNATION_LABELS", DECISION_LABELS),
                    csv_env("DECISION_STAGNATION_PACKET_LABELS", PACKET_LABELS), now)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(items, now.date(), threshold), encoding="utf-8")


def post(args):
    markdown = args.report_file.read_text(encoding="utf-8")
    marker = MARKER.fullmatch(markdown.partition("\n")[0])
    if not marker:
        raise ValueError("サマリの先頭に日付と件数の印がありません")
    if os.environ.get("DECISION_STAGNATION_DRY_RUN") != "0":
        print(markdown, end="")
        return

    gl = GitLab()
    url = env("GITLAB_HUB_ISSUE_URL")
    api_prefix = gl.url + "/api/v4/projects/"
    api_match = (re.fullmatch(r"([^/]+)/issues/([1-9][0-9]*)/?", url[len(api_prefix):])
                 if url.startswith(api_prefix) else None)
    ref = ((unquote(api_match[1]), int(api_match[2])) if api_match else issue_reference(gl.url, url))
    if not ref:
        raise ValueError("GITLAB_HUB_ISSUE_URL に同じ GitLab のハブ Issue の URL を設定してください")
    path = issue_path(*ref) + "/notes"
    for note in gl.pages(path):
        previous = MARKER.fullmatch(note["body"].partition("\n")[0])
        if previous and previous[1] == marker[1]:
            print(f"{marker[1]} のサマリは投稿済みです")
            return
    gl.call(path, note=markdown)
    print(marker[0])


def tick(args):
    args.file.parent.mkdir(parents=True, exist_ok=True)
    args.file.write_text(datetime.now(timezone.utc).date().isoformat() + "\n", encoding="utf-8")


def run(args):
    try:
        args.handler(args)
    except (GitLabError, ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        token = os.environ.get("GITLAB_TOKEN")
        message = str(exc)
        print(message.replace(token, "***") if token else message, file=sys.stderr)
        return 1
    return 0


COMMANDS = (
    ("tick", tick, "file", "定期実行の UTC 日付を記録する"),
    ("report", report, "--out", "判断待ちを取得してサマリを作る"),
    ("post", post, "report_file", "サマリをハブ Issue に投稿する"),
)


def configure(parser, handler, argument):
    parser.add_argument(argument, type=Path, **({"required": True} if argument == "--out" else {}))
    parser.set_defaults(handler=handler)


def register(api):
    for name, handler, argument, help_text in COMMANDS:
        parser = api.add_command(name, lambda _cfg, _store, args: run(args), help_text, needs_config=False)
        configure(parser, handler, argument)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    for name, handler, argument, help_text in COMMANDS:
        configure(commands.add_parser(name, help=help_text), handler, argument)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
