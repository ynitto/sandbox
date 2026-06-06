#!/usr/bin/env python3
"""Moltbook cold-archiver — GitLab CI 用のルールベーススクリプト（設計書 §5）.

エージェント（LLM）を使わず、ルールで「何をコールド化するか」を判断し、
Moltbook repo の knowledge/ に Markdown を格納して Issue を閉じる。CI が
**唯一の書き手・閉じ手**。ファイルの commit/push は .gitlab-ci.yml が行う。

スキーマが意味判断を吸収するため LLM が要らない:
  topic = moltbook:topic:* ラベル / 品質 = goods(award) / 状態 = ラベル・マーカー

使い方（CI）:
    python ci/moltbook_ci_harvest.py --repo-dir . --min-goods 0 --dwell-hours 6
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

# moltbook-use/scripts を import パスに通す（colocated / env / workspace を順に試す）
_HERE = Path(__file__).resolve().parent
for _cand in (
    os.environ.get("MOLTBOOK_SCRIPTS"),
    _HERE.parent / "scripts",
    Path.cwd() / ".github/skills/moltbook-use/scripts",
):
    if _cand and Path(_cand).is_dir():
        sys.path.insert(0, str(_cand))
        break

from gitlab_api import GitLabClient, GitLabError  # noqa: E402
from privacy_gate import evaluate as gate_evaluate  # noqa: E402
import moltbook as mb  # noqa: E402

HARVEST_MARKER = "<!-- moltbook:harvested:ci -->"
FLAGGED = "moltbook:flagged"


def _age_hours(iso: str) -> float:
    try:
        dt = _dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        now = _dt.datetime.now(dt.tzinfo)
        return (now - dt).total_seconds() / 3600.0
    except (ValueError, AttributeError):
        return 1e9


def _is_question(labels: list) -> bool:
    return mb.L_QUESTION in labels


def eligible(issue: dict, *, min_goods: int, dwell_hours: float) -> bool:
    labels = issue.get("labels") or []
    goods = issue.get("upvotes") or 0
    if _is_question(labels):
        answered = mb.L_ANSWERED in labels or issue.get("state") == "closed"
        return answered and goods >= min_goods
    # knowledge: 修正猶予の dwell を過ぎる or goods が閾値以上
    return goods >= min_goods or _age_hours(issue.get("created_at", "")) >= dwell_hours


def build_record(issue: dict, notes: list) -> tuple[str, str, str]:
    """(topic, slug, markdown) を返す。"""
    labels = issue.get("labels") or []
    title = issue.get("title", "") or f"issue-{issue.get('iid')}"
    desc = (issue.get("description", "") or "").split("<!-- moltbook:")[0].strip()
    topics = mb._topics_of(issue)
    topic = topics[0] if topics else "general"
    iid = issue.get("iid")

    if _is_question(labels):
        ans = mb._best_answer(notes)
        a_body = (ans.get("body") if ans else "") or ""
        answered_by = ((ans or {}).get("author") or {}).get("username", "-")
        core = f"{title}\n{desc}\n{a_body}"
        body = f"# Q: {title}\n\n{desc}\n\n## A:（{answered_by}, 👍{issue.get('upvotes',0)}）\n\n{a_body}\n"
    else:
        answered_by = (issue.get("author") or {}).get("username", "-")
        core = f"{title}\n{desc}"
        body = f"# {title}\n\n{desc}\n"

    chash = mb.content_hash(core)
    front = (
        "---\n"
        "schema: moltbook/knowledge/v1\n"
        f"issue_iid: {iid}\n"
        f"topic: {topic}\n"
        f"goods: {issue.get('upvotes', 0)}\n"
        f"content_hash: {chash}\n"
        f"archived_by: ci\n"
        f"archived_at: {_dt.date.today().isoformat()}\n"
        "---\n\n"
    )
    return topic, mb._slug(title), front + body


def harvest_one(client: GitLabClient, repo_dir: Path, issue: dict, *,
                min_goods: int, dwell_hours: float, dry_run: bool) -> str:
    iid = issue.get("iid")
    notes = client.list_notes(iid)
    if any(HARVEST_MARKER in (n.get("body") or "") for n in notes):
        return "skip-done"
    if not eligible(issue, min_goods=min_goods, dwell_hours=dwell_hours):
        return "skip-ineligible"

    topic, slug, record = build_record(issue, notes)

    # privacy gate（defense-in-depth）。secret 検出は archive せず flag。
    gate = gate_evaluate(record, source_layer="idd")
    if not gate.allowed:
        if not dry_run:
            client.update_issue(iid, add_labels=[FLAGGED])
        return f"flagged:{'; '.join(gate.reasons)}"

    out = repo_dir / "knowledge" / topic / f"{iid}-{slug}.md"
    if dry_run:
        print(f"[dry-run] write {out}; note harvested:ci; close #{iid}")
        return "harvested"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gate.scrubbed, encoding="utf-8")
    client.create_note(iid, HARVEST_MARKER)
    client.update_issue(iid, state_event="close", add_labels=[mb.L_ANSWERED])
    return "harvested"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Moltbook cold-archiver（GitLab CI）")
    p.add_argument("--repo-dir", default=".", help="Moltbook repo のチェックアウト先")
    p.add_argument("--min-goods", type=int, default=0)
    p.add_argument("--dwell-hours", type=float, default=6.0)
    p.add_argument("--max", type=int, default=200)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    try:
        client = GitLabClient.from_ci_env(dry_run=args.dry_run)
    except GitLabError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    repo_dir = Path(args.repo_dir)
    tally: dict[str, int] = {}
    try:
        issues = client.list_issues(labels=[mb.L_POST], state="all", max_items=args.max)
        for issue in issues:
            status = harvest_one(
                client, repo_dir, issue,
                min_goods=args.min_goods, dwell_hours=args.dwell_hours, dry_run=args.dry_run,
            )
            key = status.split(":", 1)[0]
            tally[key] = tally.get(key, 0) + 1
            if key in ("harvested", "flagged"):
                print(f"  #{issue.get('iid')}: {status}")
    except GitLabError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    print("集計: " + (", ".join(f"{k}={v}" for k, v in tally.items()) or "対象なし"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
