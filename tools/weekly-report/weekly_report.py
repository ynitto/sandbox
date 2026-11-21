#!/usr/bin/env python3
"""GitLab の活動から週次レポートとリリースノートの下書きを作る。"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from itertools import chain
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPORT_HEADING = "# 週次レポート"
RELEASE_HEADING = "# リリースノート下書き"
RULES = (
    ("修正", r"\b(?:fix(?:e[ds])?|bug(?:fix)?|hotfix)\b|修正|不具合"),
    ("新機能", r"\b(?:feat(?:ure)?|enhancement)\b|新機能|機能追加"),
    ("改善", r"\b(?:improve(?:ment)?|perf(?:ormance)?|refactor|docs?)\b|改善|最適化"),
)
PREFIX = re.compile(r"^(?:feat(?:ure)?|fix|bugfix|hotfix|improve(?:ment)?|perf|"
                    r"refactor|docs?)(?:\([^()]*\))?!?:\s*", re.IGNORECASE)
ISSUE_REFERENCE = re.compile(r"(?<![\w/#])(?:[\w.-]+(?:/[\w.-]+)+)?#[1-9][0-9]*\b"
                             r"|https?://[^\s<>()\"\[\]]+/-/issues/[1-9][0-9]*\b")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitLab:
    def __init__(self, url, token):
        self.api = url.rstrip("/") + "/api/v4"
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, path, *, query=None, body=None):
        suffix = "?" + urlencode(query) if query else ""
        request = Request(self.api + path + suffix, headers={
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }, data=json.dumps(body).encode("utf-8") if body is not None else None)
        with self.opener.open(request, timeout=30) as response:
            return json.load(response), response.headers

    def pages(self, path, **query):
        page = 1
        while True:
            items, headers = self.request(path, query=dict(query, per_page=100, page=page))
            if not isinstance(items, list):
                raise ValueError(f"GitLab の一覧応答が不正です ({path})")
            yield from items
            next_page = headers.get("X-Next-Page")
            if next_page is None and len(items) == 100:
                # 一部の環境ではページ番号のヘッダーが省略される。
                page += 1
            elif next_page and int(next_page) > page:
                page = int(next_page)
            else:
                return


def valid_project(project):
    value = str(project)
    if re.fullmatch(r"[1-9][0-9]*", value):
        return True
    parts = value.split("/")
    return len(parts) >= 2 and all(
        part not in (".", "..") and re.fullmatch(r"[\w.-]+", part) for part in parts
    )


def project_path(project):
    return "/projects/" + quote(str(project), safe="")


def hub_notes_path(base_url, issue_url):
    base = urlsplit(base_url)
    hub = urlsplit(issue_url)
    if (base.scheme not in ("http", "https") or not base.hostname
            or base.username is not None or base.query or base.fragment
            or (hub.scheme, hub.netloc) != (base.scheme, base.netloc)):
        raise ValueError("hub_issue_url には gitlab_url と同じ GitLab の issue URL を指定してください")
    prefix = base.path.rstrip("/")
    if not hub.path.startswith(prefix + "/"):
        raise ValueError("hub_issue_url が gitlab_url の配下にありません")
    relative = hub.path[len(prefix):].rstrip("/")
    api_match = re.fullmatch(r"/api/v4/projects/([^/]+)/issues/([1-9][0-9]*)", relative)
    web_match = re.fullmatch(r"/(.+)/-/issues/([1-9][0-9]*)", relative)
    match = api_match or web_match
    if not match:
        raise ValueError("hub_issue_url に issue のブラウザ URL または API URL を指定してください")
    project, iid = match.groups()
    project = unquote(project)
    if not valid_project(project):
        raise ValueError("hub_issue_url のプロジェクトが不正です")
    return f"{project_path(project)}/issues/{iid}/notes"


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def marker(self):
        return f"[weekly-report: {self.start.isoformat()}/{self.end.isoformat()}]"

    def contains(self, timestamp):
        if not timestamp:
            return False
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return utc_midnight(self.start) <= value < utc_midnight(self.end)


def utc_midnight(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


@dataclass
class ProjectReport:
    project: str
    mrs: list
    issues: list
    pipeline_total: int
    pipeline_success: int
    open_counts: tuple[int, int] | None


def related_issues(gl, path, mr):
    references = {}
    try:
        closing = list(gl.pages(f"{path}/merge_requests/{mr['iid']}/closes_issues"))
    except HTTPError as exc:
        if exc.code != 404:
            raise
        closing = []
    for issue in closing:
        url = issue.get("web_url")
        if url:
            references[url] = (f"#{issue['iid']}", url)
        elif issue.get("id"):
            references[str(issue["id"])] = (str(issue["id"]), None)

    for field in ("title", "description"):
        for ref in ISSUE_REFERENCE.findall(mr.get(field) or ""):
            if ref.startswith(("http://", "https://")):
                references.setdefault(ref, ("#" + ref.rsplit("/", 1)[1], ref))
            else:
                mr_url = mr.get("web_url") or ""
                base = mr_url.split("/-/merge_requests/")[0]
                project, _, iid = ref.rpartition("#")
                url = None
                if base and not project:
                    url = f"{base}/-/issues/{iid}"
                references.setdefault(url or ref, (ref, url))
    return list(references.values())


def collect(gl, projects, period, include_open=False):
    since = utc_midnight(period.start).isoformat()
    reports = []
    for project in projects:
        path = project_path(project)
        mrs = [mr for mr in gl.pages(path + "/merge_requests", state="merged", scope="all",
                                     updated_after=since)
               if period.contains(mr.get("merged_at"))]
        issues = [issue for issue in gl.pages(path + "/issues", state="closed", scope="all",
                                             updated_after=since)
                  if period.contains(issue.get("closed_at"))]
        mrs = [(mr, related_issues(gl, path, mr)) for mr in sorted(mrs, key=lambda mr: mr["iid"])]
        issues.sort(key=lambda issue: issue["iid"])
        total = success = 0
        seen_pipeline_ids = set()
        pipelines = chain(
            gl.pages(path + "/pipelines", updated_after=since),
            gl.pages(path + "/pipelines", updated_after=since, source="parent_pipeline"),
        )
        for pipeline in pipelines:
            pipeline_id = pipeline["id"]
            if pipeline_id in seen_pipeline_ids:
                continue
            seen_pipeline_ids.add(pipeline_id)
            if period.contains(pipeline["created_at"]):
                total += 1
                success += pipeline["status"] == "success"

        open_counts = None
        if include_open:
            open_counts = tuple(sum(1 for _ in gl.pages(path + "/" + endpoint,
                                                        state="opened", scope="all"))
                                for endpoint in ("merge_requests", "issues"))
        reports.append(ProjectReport(str(project), mrs, issues, total, success, open_counts))
    return reports


def markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", escape(" ".join(value.split()), quote=False))


def link(label, url):
    label = markdown_text(label)
    if not url or urlsplit(url).scheme not in ("http", "https"):
        return label
    return f"[{label}](<{quote(url, safe=':/?&=%#+@,;~')}>)"


def classify(mr):
    text = " ".join([mr["title"], *(mr.get("labels") or [])])
    for category, pattern in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return "その他"


def pipeline_rate(success, total):
    return f"{success}/{total}（{success / total:.1%}）" if total else "0/0（対象なし）"


def generate(period, reports):
    total = sum(report.pipeline_total for report in reports)
    success = sum(report.pipeline_success for report in reports)
    weekly = [
        period.marker, "", REPORT_HEADING, "",
        f"期間（UTC） {period.start} 00:00 以上、{period.end} 00:00 未満", "",
        "## 全体サマリ", "",
        f"- マージ済み MR {sum(len(report.mrs) for report in reports)} 件",
        f"- クローズした issue {sum(len(report.issues) for report in reports)} 件",
        f"- パイプライン成功率 {pipeline_rate(success, total)}",
    ]
    categories = {category: [] for category, _ in RULES}
    categories["その他"] = []
    for report in reports:
        project = markdown_text(report.project)
        weekly.extend(["", f"## {project}", "",
                       f"- パイプライン成功率 {pipeline_rate(report.pipeline_success, report.pipeline_total)}"])
        if report.open_counts is not None:
            mrs, issues = report.open_counts
            weekly.append(f"- 取得時点のオープン件数 MR {mrs} 件、issue {issues} 件")
        weekly.extend(["", f"### マージ済み MR（{len(report.mrs)} 件）", ""])
        for mr, references in report.mrs:
            ref = link(f"!{mr['iid']}", mr.get("web_url"))
            author = mr.get("author") or {}
            name = markdown_text(author.get("name") or author.get("username") or "不明")
            line = f"- {ref} {markdown_text(mr['title'])}（作成者 {name}）"
            if references:
                line += " — 関連 issue " + "、".join(link(label, url) for label, url in references)
            weekly.append(line)
            title = PREFIX.sub("", mr["title"]).strip() or mr["title"]
            categories[classify(mr)].append(f"- {markdown_text(title)}（{project} {ref}）")
        if not report.mrs:
            weekly.append("- なし")
        weekly.extend(["", f"### クローズした issue（{len(report.issues)} 件）", ""])
        for issue in report.issues:
            ref = link(f"#{issue['iid']}", issue.get("web_url"))
            weekly.append(f"- {ref} {markdown_text(issue['title'])}")
        if not report.issues:
            weekly.append("- なし")

    release = [RELEASE_HEADING, "", f"対象期間（UTC） {period.start} 〜 {period.end}（終了日を除く）"]
    for category in ("新機能", "改善", "修正", "その他"):
        release.extend(["", f"## {category}", "", *(categories[category] or ["- なし"])])
    return "\n".join(weekly), "\n".join(release)


def post(gl, notes_path, period, body, *, enabled=False):
    if not enabled:
        return "dry-run"
    for note in gl.pages(notes_path):
        first_line = note["body"].split("\n", 1)[0].rstrip("\r")
        if first_line == period.marker:
            return "duplicate"
    gl.request(notes_path, body={"body": body})
    return "posted"


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("設定は JSON オブジェクトで指定してください")
    for key in ("gitlab_url", "token_env", "hub_issue_url"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"{key} に空でない文字列を指定してください")
    projects = config.get("projects")
    if not isinstance(projects, list) or not projects or any(
        type(project) not in (int, str) or not valid_project(project) for project in projects
    ):
        raise ValueError("projects にプロジェクトの ID または名前空間付きパスを指定してください")
    config["projects"] = list(dict.fromkeys(map(str, projects)))
    for key in ("post_enabled", "include_open"):
        if type(config.get(key, False)) is not bool:
            raise ValueError(f"{key} は true または false で指定してください")
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON 設定ファイル")
    parser.add_argument("--days", type=int, help="集約日数")
    parser.add_argument("--end-date", type=date.fromisoformat, help="期間の終了日（UTC、集約に含めない）")
    parser.add_argument("--dry-run", action="store_true", help="設定や環境変数にかかわらず表示のみ")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config.expanduser())
        days = args.days if args.days is not None else config.get("period_days", 7)
        if type(days) is not int or days <= 0:
            raise ValueError("period_days / --days は正の整数で指定してください")
        end = args.end_date or datetime.now(timezone.utc).date()
        period = Period(end - timedelta(days=days), end)
        notes_path = hub_notes_path(config["gitlab_url"], config["hub_issue_url"])
        token_env = config["token_env"]
        token = os.environ.get(token_env)
        if not token:
            raise ValueError(f"トークンを環境変数 {token_env} に設定してください")
        override = os.environ.get("WEEKLY_REPORT_POST")
        if not args.dry_run and override not in (None, "0", "1"):
            raise ValueError("WEEKLY_REPORT_POST は 0 または 1 で指定してください")
        enabled = not args.dry_run and (config.get("post_enabled", False) if override is None
                                        else override == "1")

        gl = GitLab(config["gitlab_url"], token)
        reports = collect(gl, config["projects"], period, config.get("include_open", False))
        body = "\n\n".join(generate(period, reports))
        result = post(gl, notes_path, period, body, enabled=enabled)
        if result == "dry-run":
            print(body)
        elif result == "duplicate":
            print(f"投稿済みのためスキップ {period.marker}")
        else:
            print(f"ハブ issue に投稿しました {period.marker}")
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(f"週次レポートの処理に失敗しました ({exc})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
