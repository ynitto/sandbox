"""マージ済みの GitLab MR から付随作業の下書きを作る。"""
from __future__ import annotations

import argparse
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
ISSUE_REFERENCE = re.compile(
    r"\b(?:clos(?:e[sd]?|ing)|fix(?:e[sd]?|ing)?|resolv(?:e[sd]?|ing))\b"
    r":?(?:[^\S\r\n]+|[^\S\r\n]*\r?\n[^\S\r\n]*)"
    r"(?:#(?P<iid>[1-9][0-9]*)\b|(?:\[[^\]\r\n]*\]\()?<?(?P<url>https?://[^\s<>\[\]()`]+))",
    re.IGNORECASE,
)
MARKER = re.compile(r"【下書き】マージ後処理（MR ![1-9][0-9]*）")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


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

    def pages(self, path):
        page = 1
        result = []
        while True:
            items, headers = self.call(path, query={"per_page": 100, "page": page})
            if not isinstance(items, list):
                raise ValueError("GitLab の一覧の応答が不正です")
            result.extend(items)
            next_page = headers.get("X-Next-Page")
            if next_page == "" or (next_page is None and len(items) < 100):
                return result
            following = page + 1 if next_page is None else int(next_page)
            if following <= page:
                raise ValueError("GitLab の次ページ番号が不正です")
            page = following


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


def related_issues(gl, mr_path, project, description):
    try:
        issues = gl.pages(mr_path + "/closes_issues")
    except GitLabError as exc:
        if exc.status not in {404, 405, 501}:
            raise
        references = {}
        for match in ISSUE_REFERENCE.finditer(HTML_COMMENT.sub(" ", description)):
            ref = ((project, int(match["iid"])) if match["iid"] else
                   issue_reference(gl.url, match["url"].rstrip(".,;:!?")))
            if ref:
                references[ref] = None
        issues = [gl.get(issue_path(*ref)) for ref in references]

    unique = {}
    for issue in issues:
        item = {key: issue[key] for key in ("project_id", "iid", "web_url", "state")}
        unique[(item["project_id"], item["iid"])] = item
    return sorted(unique.values(), key=lambda issue: (issue["project_id"], issue["iid"]))


def one_line(value):
    return " ".join(value.split())


def render(mr, issues, commits, project):
    iid = mr["iid"]
    title = one_line(mr["title"])
    description = HTML_COMMENT.sub("", mr.get("description") or "")
    details = one_line(description)
    subjects = list(dict.fromkeys(one_line(commit["title"]) for commit in commits))
    changes = list(dict.fromkeys(text for text in (details, *subjects) if text and text != title))
    summary = " / ".join([title, *changes])
    mr_link = f"[MR !{iid}](<{mr['web_url']}>)"
    lines = [f"【下書き】マージ後処理（MR !{iid}）", "", f"対象: {mr_link}", ""]
    if issues:
        lines.extend(["## クローズ確認コメントの下書き", ""])
    for issue in issues:
        state = {"opened": "未クローズ", "closed": "クローズ済み"}.get(issue["state"], issue["state"])
        lines.extend([
            f"### [Issue #{issue['iid']}](<{issue['web_url']}>)（{state}）",
            "",
            f"{mr_link} がマージされました。取り込まれた変更: {summary}",
            "未クローズの場合はこの Issue を閉じてよいか、クローズ済みの場合はそのままでよいか確認してください。",
            "",
        ])
    tool, separator, heading = title.partition(":")
    if not separator or not tool.strip() or not heading.strip():
        tool, heading = project.rsplit("/", 1)[-1], title
    lines.extend(["## CHANGELOG 追記案（[Unreleased]）", "", f"### {tool.strip()}: {heading.strip()}", ""])
    lines.extend(f"- {text}" for text in (changes or [title]))
    return "\n".join(lines) + "\n"


def draft(args):
    change = json.loads(args.change if args.change != "-" else sys.stdin.read())
    record = change["record"]
    iid = record["iid"]
    if type(iid) is not int or iid <= 0:
        raise ValueError("MR IID は正の整数です")
    if change.get("kind") == "removed" or record["state"] != "merged":
        raise ValueError(f"MR !{iid} はマージ済みの変化ではありません")
    if args.out.resolve() == args.json_out.resolve():
        raise ValueError("--out と --json-out には別々のパスを指定してください")

    gl = GitLab()
    project = env("GITLAB_PROJECT")
    mr_path = f"{quote(project, safe='')}/merge_requests/{iid}"
    mr = gl.get(mr_path)
    if mr["iid"] != iid or mr["state"] != "merged":
        raise ValueError(f"MR !{iid} のマージ済み状態を GitLab で確認できませんでした")
    issues = related_issues(gl, mr_path, project, mr.get("description") or "")
    commits = gl.pages(mr_path + "/commits")
    markdown = render(mr, issues, commits, project)
    kinds = ["issue_close_confirmation", "changelog"] if issues else ["changelog"]
    summary = json.dumps({"mr_iid": iid, "related_issues": issues, "draft_types": kinds}, ensure_ascii=False)
    for path, content in ((args.out, markdown), (args.json_out, summary + "\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(summary)


def post(args):
    markdown = args.draft_file.read_text(encoding="utf-8")
    if not MARKER.fullmatch(markdown.partition("\n")[0]):
        raise ValueError("下書きの先頭に MR の印がありません")
    if os.environ.get("MERGE_FOLLOWUP_DRY_RUN") != "0":
        print(f"dry-run: {args.draft_file}")
        return
    gl = GitLab()
    url = env("GITLAB_HUB_ISSUE_URL")
    api_prefix = gl.url + "/api/v4/projects/"
    api_match = (re.fullmatch(r"([^/]+)/issues/([1-9][0-9]*)/?", url[len(api_prefix):])
                 if url.startswith(api_prefix) else None)
    ref = ((unquote(api_match[1]), int(api_match[2])) if api_match else issue_reference(gl.url, url))
    if not ref:
        raise ValueError("GITLAB_HUB_ISSUE_URL に同じ GitLab のハブ Issue の URL を設定してください")
    gl.call(issue_path(*ref) + "/notes", note=markdown)
    print(markdown.partition("\n")[0])


def run(args):
    try:
        args.handler(args)
    except (GitLabError, ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        token = os.environ.get("GITLAB_TOKEN")
        message = str(exc)
        print(message.replace(token, "***") if token else message, file=sys.stderr)
        return 1
    return 0


def draft_arguments(parser):
    parser.add_argument("change", help="change-watch get --one の JSON（標準入力は -）")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.set_defaults(handler=draft)


def post_arguments(parser):
    parser.add_argument("draft_file", type=Path)
    parser.set_defaults(handler=post)


def register(api):
    for name, arguments, help_text in COMMANDS:
        arguments(api.add_command(name, lambda _cfg, _store, args: run(args), help_text, needs_config=False))


COMMANDS = (
    ("draft", draft_arguments, "マージ後処理の下書きを作る"),
    ("post", post_arguments, "下書きをハブ Issue に投稿する"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    for name, arguments, help_text in COMMANDS:
        arguments(commands.add_parser(name, help=help_text))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
