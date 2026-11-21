#!/usr/bin/env python3
"""スキルレジストリを読み取り、改良提案の下書きを作る。"""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
from itertools import takewhile
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERDICTS = ("ok", "needs-improvement", "broken")
PROBLEMS = VERDICTS[1:]


@dataclass
class SkillSummary:
    name: str
    installed_at: str
    counts: Counter
    latest_at: str
    unrefined: int
    threshold: int
    pending: bool
    streak: int
    latest_broken: dict | None
    problems: list[dict]

    @property
    def needs_refinement(self):
        return self.pending or self.unrefined >= self.threshold


def default_registry_path():
    scripts = Path(__file__).resolve().parents[2] / ".github/skills/git-skill-manager/scripts"
    sys.path.insert(0, str(scripts))
    try:
        from registry import _registry_path

        return _registry_path()
    finally:
        sys.path.pop(0)


def read_object(path):
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("JSON のルートはオブジェクトである必要があります")
    return data


def timestamp(entry):
    value = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def summarize(registry):
    skills = registry["installed_skills"]
    if not isinstance(skills, list):
        raise ValueError("installed_skills は配列である必要があります")
    summaries = []
    for skill in skills:
        name = skill["name"]
        history = skill.get("feedback_history", [])
        threshold = skill.get("refine_threshold", 1 if skill.get("source_repo") == "workspace" else 3)
        if not isinstance(name, str) or not name.strip() or not isinstance(history, list):
            raise ValueError("スキル名または feedback_history が不正です")
        if type(threshold) is not int or threshold < 1:
            raise ValueError("refine_threshold は正の整数である必要があります")
        history = sorted(history, key=timestamp)
        counts = Counter(entry["verdict"] for entry in history)
        if counts.keys() - set(VERDICTS):
            raise ValueError("未対応の verdict が含まれています")
        problems = [entry for entry in reversed(history) if entry["verdict"] in PROBLEMS]
        streak = sum(1 for _ in takewhile(
            lambda entry: entry["verdict"] == "needs-improvement", reversed(history)
        ))
        summaries.append(SkillSummary(
            name=name,
            installed_at=skill.get("installed_at") or "",
            counts=counts,
            latest_at=history[-1]["timestamp"] if history else "",
            unrefined=sum(1 for entry in problems if not entry.get("refined")),
            threshold=threshold,
            pending=bool(skill.get("pending_refinement")),
            streak=streak,
            latest_broken=next((entry for entry in problems if entry["verdict"] == "broken"), None),
            problems=problems,
        ))
    return sorted(summaries, key=lambda skill: skill.name)


def markdown_text(value):
    line = " ".join(str(value).split())
    return re.sub(r"([\\`*_~\[\]|<>@&#])", r"\\\1", line)


def note_excerpt(entry):
    note = " ".join(str(entry.get("note") or "").split())
    if len(note) > 120:
        note = note[:120] + "…"
    return markdown_text(note or "note なし")


def proposal(skill):
    relevant = [entry for entry in skill.problems if not entry.get("refined")][:3]
    if skill.latest_broken and skill.latest_broken not in relevant:
        relevant.append(skill.latest_broken)
    if not relevant:
        relevant = skill.problems[:3]
    excerpts = "、".join(
        f"{entry['verdict']}（{note_excerpt(entry)}）"
        for entry in relevant
    ) or "問題の note なし"
    if skill.unrefined and (skill.needs_refinement or (
        skill.latest_broken and not skill.latest_broken.get("refined")
    )):
        action = "git-skill-manager の refine を実行し、該当する手順を再検証する。"
    else:
        action = "skill-evaluator で現行版を評価し、改良の要否を確認する。"
    return f"- **{markdown_text(skill.name)}** — {excerpts}。次の一手は {action}"


def render_digest(skills, day):
    lines = [
        f"<!-- skill-feedback-digest:{day} -->",
        f"# スキルフィードバック集計（{day} UTC）",
        "",
        "| スキル | ok | needs-improvement | broken | 最終フィードバック | 未改良問題 |",
        "| --- | ---: | ---: | ---: | --- | ---: |",
    ]
    for skill in skills:
        counts = " | ".join(str(skill.counts[verdict]) for verdict in VERDICTS)
        lines.append(
            f"| {markdown_text(skill.name)} | {counts} | "
            f"{markdown_text(skill.latest_at or '—')} | {skill.unrefined} |"
        )

    def section(title, rows):
        lines.extend(["", f"## {title}", ""])
        lines.extend(list(rows) or ["該当なし"])

    section("broken の記録あり", (
        f"- **{markdown_text(s.name)}** — {markdown_text(s.latest_broken['timestamp'])}、"
        f"{note_excerpt(s.latest_broken)}"
        for s in skills if s.latest_broken
    ))
    section("改良待ちまたはしきい値到達", (
        f"- **{markdown_text(s.name)}** — 未改良 {s.unrefined} 件、しきい値 {s.threshold} 件、"
        f"pending_refinement={str(s.pending).lower()}"
        for s in skills if s.needs_refinement
    ))
    section("needs-improvement が連続", (
        f"- **{markdown_text(s.name)}** — 直近 {s.streak} 件連続" for s in skills if s.streak >= 2
    ))
    section("フィードバックなし（未使用候補）", (
        f"- **{markdown_text(s.name)}** — 登録日時 {markdown_text(s.installed_at or '不明')}"
        for s in skills if not s.counts
    ))
    section("改良提案リスト（下書き）", (
        proposal(s) for s in skills if s.latest_broken or s.needs_refinement or s.streak >= 2
    ))
    return "\n".join(lines) + "\n"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def notes_endpoint(base_url, issue_url):
    if not isinstance(issue_url, str):
        raise ValueError("hub_issue_url は文字列で指定してください")
    base = urlsplit(base_url.rstrip("/"))
    issue = urlsplit(issue_url)
    if (
        base.scheme != "https" or not base.hostname or base.username is not None
        or base.query or base.fragment or issue.netloc != base.netloc
        or issue.scheme != base.scheme or not issue.path.startswith(base.path + "/")
    ):
        raise ValueError("同じ GitLab の HTTPS URL を指定してください")
    path = issue.path[len(base.path):]
    match = re.fullmatch(r"/(.+/.+)/-/issues/([1-9][0-9]*)/?", path)
    if not match:
        raise ValueError("ハブ issue の URL を指定してください")
    project, iid = match.groups()
    return f"{base.geturl()}/api/v4/projects/{quote(unquote(project), safe='')}/issues/{iid}/notes"


def post_digest(markdown, issue_url):
    endpoint = notes_endpoint(os.environ.get("GITLAB_URL", ""), issue_url)
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if not token:
        raise ValueError("GITLAB_TOKEN を設定してください")
    opener = build_opener(NoRedirect())
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    marker = markdown.splitlines()[0]
    page = 1
    while True:
        query = urlencode({"per_page": 100, "page": page})
        with opener.open(Request(f"{endpoint}?{query}", headers=headers), timeout=30) as response:
            notes = json.load(response)
            next_page = response.headers.get("X-Next-Page")
        if not isinstance(notes, list) or any(
            not isinstance(note, dict) or not isinstance(note.get("body"), str) for note in notes
        ):
            raise ValueError("GitLab のコメント一覧が不正です")
        if any(note["body"].partition("\n")[0].rstrip("\r") == marker for note in notes):
            return False
        if next_page == "" or (next_page is None and len(notes) < 100):
            break
        following = int(next_page) if next_page is not None else page + 1
        if following <= page:
            raise ValueError("GitLab の次ページ番号が不正です")
        page = following

    data = json.dumps({"body": markdown}, ensure_ascii=False).encode("utf-8")
    with opener.open(Request(endpoint, data=data, headers=headers), timeout=30):
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON 設定ファイル")
    parser.add_argument("--registry", help="レジストリ JSON のパス")
    parser.add_argument("--dry-run", action="store_true", help="設定にかかわらず投稿を見送る")
    args = parser.parse_args(argv)

    try:
        config = read_object(args.config.expanduser()) if args.config else {}
        dry_run = config.get("dry_run", True)
        if type(dry_run) is not bool:
            raise ValueError("dry_run は true または false を指定してください")
        registry_path = args.registry or os.environ.get("SKILL_FEEDBACK_REGISTRY_PATH")
        if not registry_path:
            registry_path = config.get("registry_path") or default_registry_path()
        path = Path(registry_path).expanduser()
    except (OSError, ValueError, TypeError, RuntimeError, ImportError) as exc:
        print(f"設定エラー（{exc}）", file=sys.stderr)
        return 1

    try:
        skills = summarize(read_object(path))
    except FileNotFoundError:
        print(f"未確認（レジストリがありません: {path}）")
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        print(f"未確認（レジストリを読めません: {exc}）")
        return 0

    markdown = render_digest(skills, datetime.now(timezone.utc).date())
    print(markdown, end="")
    if dry_run or args.dry_run:
        print("dry-run（投稿なし）", file=sys.stderr)
        return 0

    try:
        issue_url = config.get("hub_issue_url") or os.environ.get("GITLAB_HUB_ISSUE_URL", "")
        posted = post_digest(markdown, issue_url)
    except HTTPError as exc:
        exc.close()
        print(f"投稿処理エラー（GitLab HTTP {exc.code}）", file=sys.stderr)
        return 1
    except (OSError, http.client.HTTPException, TypeError, ValueError) as exc:
        print(f"投稿処理エラー（{exc}）", file=sys.stderr)
        return 1

    print("投稿しました" if posted else "同日の投稿があるためスキップしました", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
