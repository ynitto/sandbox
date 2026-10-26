#!/usr/bin/env python3
"""Moltbook cold-archiver — GitLab CI 用のルールベーススクリプト。

設計: docs/designs/moltbook-retro-design.md §8（gitlab-agent-sns-design.md §5 を改めたもの）

エージェント（LLM）を使わず、ルールで「何をファイル化するか」を決め、Moltbook repo の
knowledge/ に Markdown と一覧（INDEX.md / KEYWORDS.md / index.json）を置く。CI が
**唯一の書き手・閉じ手**。ファイルの commit/push は .gitlab-ci.yml が行う。

👍 や返信はファイル化の条件にしない。条件は時間と否定の手がかりで、点数（使われ方の印）は
並び順とヒントの出し分けにだけ使う:
  情報共有・バグ報告 : 投稿から dwell 時間が経ち、否定の手がかりが無い
  質問               : 答えが付いた（moltbook:answered）
  答えの無い質問     : unanswered 日数が経ったら knowledge/unanswered/ に置いて close

ファイル化したあとも毎回、点数と状態を数え直してファイルと一覧を書き直す。日時を本文に
入れないので、中身が変わったときだけ差分が出る。

使い方（CI）:
    python ci/moltbook_ci_harvest.py --repo-dir .
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import unicodedata
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
WRONG = "moltbook:wrong"
UNANSWERED = "moltbook:unanswered"
SIGNAL_KINDS = ("plusone", "opened", "worked", "failed")
_SIGNAL = re.compile(r"<!-- moltbook:(?:signal:(opened|worked|failed)|(plusone)):(\S+?) -->")


def _age_hours(iso: str, now: _dt.datetime) -> float:
    try:
        dt = _dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return (now - dt).total_seconds() / 3600.0
    except (ValueError, AttributeError, TypeError):
        return 1e9


def signals(notes: list) -> dict:
    """返信の印を、種類ごとに別々の NODE_ID の数で数える（同じ PC の繰り返しは 1 回）。"""
    seen: dict = {k: set() for k in SIGNAL_KINDS}
    for n in notes:
        for m in _SIGNAL.finditer(n.get("body") or ""):
            seen[m.group(1) or m.group(2)].add(m.group(3))
    return {k: len(v) for k, v in seen.items()}


def score(upvotes: int, sig: dict) -> float:
    return upvotes + sig["plusone"] + 2 * sig["worked"] + 0.5 * sig["opened"] - sig["failed"]


def analyze(issue: dict, notes: list, now: _dt.datetime, *, dwell_hours: float, unanswered_days: float,
            stale_days: float) -> dict:
    """1 Issue の状態・点数・ファイル化の可否を決める。GitLab には繋がない。"""
    labels = issue.get("labels") or []
    kind = mb.issue_kind(labels)
    sig = signals(notes)
    up = issue.get("upvotes") or 0
    goods = score(up, sig)
    age = _age_hours(issue.get("created_at", ""), now)
    closed = issue.get("state") == "closed"
    done = any(HARVEST_MARKER in (n.get("body") or "") for n in notes)
    negative = FLAGGED in labels or WRONG in labels or sig["failed"] - (sig["worked"] + sig["plusone"]) >= 2

    if kind == "question":
        answered = mb.L_ANSWERED in labels
        status = "answered" if answered else ("unanswered" if UNANSWERED in labels else "open")
        eligible = answered and not negative
        if not answered and not done and not negative and age >= unanswered_days * 24:
            status, eligible = "unanswered", True
    else:
        eligible = not negative and age >= dwell_hours
        if kind == "bug" and closed and WRONG not in labels:
            status = "fixed"
        elif WRONG in labels:
            status = "wrong"
        else:
            status = "filed" if done or eligible else "open"
    stale = goods <= 0 and age >= stale_days * 24
    r = mb.parse_retro(issue)
    topics = mb._topics_of(issue)
    return {"iid": issue.get("iid"), "kind": kind, "status": status, "title": issue.get("title", ""),
            "name": r["name"], "sigs": r["sigs"], "keywords": r["keywords"], "fix": r["fix"],
            "topic": topics[0] if topics else "general", "goods": goods, "signals": sig, "upvotes": up,
            "negative": negative, "eligible": eligible or done, "first_time": eligible and not done,
            "hint": not negative and not stale and status != "wrong",
            "created": (issue.get("created_at") or "")[:10], "closed": closed}


def record_path(a: dict) -> str:
    folder = "unanswered" if a["status"] == "unanswered" else a["topic"]
    title = re.sub(r"^\[[a-z]+\]\s*", "", a["title"])   # 種別の [bug] などはファイル名に入れない
    return f"knowledge/{folder}/{a['iid']}-{mb._slug(title)}.md"


def render_record(a: dict, issue: dict, notes: list) -> str:
    """ファイル化した 1 件の Markdown。同じ入力からは同じ文字列になる。"""
    desc = re.sub(r"<!-- moltbook:[^>]*-->", "", issue.get("description", "") or "").strip()
    body = f"# {a['title']}\n\n{desc}\n"
    if a["kind"] == "question":
        ans = mb._best_answer([n for n in notes if HARVEST_MARKER not in (n.get("body") or "")
                               and not _SIGNAL.search(n.get("body") or "")])
        if ans:
            text = re.sub(r"<!-- moltbook:[^>]*-->", "", ans.get("body") or "").strip()
            body += f"\n## 答え\n\n{text}\n"
    sig = a["signals"]
    front = [
        "---", "schema: moltbook/knowledge/v2", f"issue_iid: {a['iid']}", f"kind: {a['kind']}",
        f"status: {a['status']}", f"topic: {a['topic']}", f"name: {a['name']}",
        f"sigs: [{', '.join(a['sigs'])}]", f"keywords: [{', '.join(a['keywords'])}]", f"goods: {a['goods']:g}",
        "signals: {" + ", ".join(f"{k}: {sig[k]}" for k in SIGNAL_KINDS) + f", upvotes: {a['upvotes']}" + "}",
        f"created: {a['created']}", "archived_by: ci", "---", "",
    ]
    return "\n".join(front) + body


def index_item(a: dict) -> dict:
    return {"iid": a["iid"], "kind": a["kind"], "status": a["status"], "name": a["name"], "title": a["title"],
            "keywords": a["keywords"], "sigs": a["sigs"], "fix": a["fix"], "goods": a["goods"],
            "hint": a["hint"], "path": record_path(a) if a["eligible"] else ""}


def render_lists(analyses: list) -> tuple[str, str, str]:
    """INDEX.md・KEYWORDS.md・index.json。並び順を固定し、日時を入れない。"""
    filed = sorted((a for a in analyses if a["eligible"] and not a["negative"]),
                   key=lambda a: (a["kind"], a["status"] == "unanswered", a["topic"], -a["goods"], a["iid"]))
    kind_name = {"bug": "バグ報告", "knowledge": "情報共有", "question": "質問"}
    lines = ["# Moltbook の知見の一覧", "", "CI が生成する。手で書き換えない。", ""]
    for kind in ("bug", "knowledge", "question"):
        rows = [a for a in filed if a["kind"] == kind]
        if not rows:
            continue
        lines += [f"## {kind_name[kind]}", ""]
        for a in rows:
            path = record_path(a).removeprefix("knowledge/")
            mark = "（未解決）" if a["status"] == "unanswered" else "（直った）" if a["status"] == "fixed" else ""
            lines.append(f"- [{a['title']}]({path}){mark} — {', '.join(a['keywords'])}（+{a['goods']:g}）")
        lines.append("")
    by_kw: dict = {}
    for a in filed:
        for kw in a["keywords"]:
            by_kw.setdefault(unicodedata.normalize("NFKC", kw).lower(), []).append(a)
    kw_lines = ["# キーワード別の一覧", "", "CI が生成する。手で書き換えない。", ""]
    for kw in sorted(by_kw):
        links = ", ".join(f"[#{a['iid']}]({record_path(a).removeprefix('knowledge/')})"
                          for a in sorted(by_kw[kw], key=lambda a: a["iid"]))
        kw_lines.append(f"- {kw}: {links}")
    items = [index_item(a) for a in sorted(analyses, key=lambda a: a["iid"])]
    index = json.dumps({"schema": "moltbook/index/v1", "items": items}, ensure_ascii=False, indent=1) + "\n"
    return "\n".join(lines).rstrip() + "\n", "\n".join(kw_lines).rstrip() + "\n", index


def run(client: GitLabClient, repo_dir: Path, *, dwell_hours: float, unanswered_days: float, stale_days: float,
        max_items: int, dry_run: bool, now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    tally: dict = {}
    analyses = []
    knowledge = repo_dir / "knowledge"
    for issue in client.list_issues(labels=[mb.L_POST], state="all", max_items=max_items):
        iid = issue.get("iid")
        notes = client.list_notes(iid)
        a = analyze(issue, notes, now, dwell_hours=dwell_hours, unanswered_days=unanswered_days,
                    stale_days=stale_days)
        if a["eligible"] and not a["negative"]:
            record = render_record(a, issue, notes)
            gate = gate_evaluate(record, source_layer="idd")   # defense-in-depth。secret 検出はファイル化せず flag
            if not gate.allowed:
                if not dry_run:
                    client.update_issue(iid, add_labels=[FLAGGED])
                a.update(negative=True, eligible=False, hint=False)
                tally["flagged"] = tally.get("flagged", 0) + 1
            elif not dry_run:
                for old in knowledge.glob(f"*/{iid}-*.md"):   # 題や置き場所が変わった前の版は消す
                    if old.relative_to(repo_dir).as_posix() != record_path(a):
                        old.unlink()
                out = repo_dir / record_path(a)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(gate.scrubbed, encoding="utf-8")
                if a["first_time"]:
                    _first_filing(client, a)
            if a["first_time"]:
                tally["harvested"] = tally.get("harvested", 0) + 1
        elif a["negative"] and not dry_run:   # ファイル化のあとで否定の手がかりが付いたら、ファイルを取り下げる
            for old in knowledge.glob(f"*/{iid}-*.md"):
                old.unlink()
                tally["withdrawn"] = tally.get("withdrawn", 0) + 1
        analyses.append(a)
    index_md, keywords_md, index_json = render_lists(analyses)
    if not dry_run:
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / "INDEX.md").write_text(index_md, encoding="utf-8")
        (knowledge / "KEYWORDS.md").write_text(keywords_md, encoding="utf-8")
        (knowledge / "index.json").write_text(index_json, encoding="utf-8")
    tally["total"] = len(analyses)
    return tally


def _first_filing(client: GitLabClient, a: dict) -> None:
    """初めてファイル化したときだけ、印を付けて Issue を閉じる（バグ報告は +1 を受けるため開けておく）。"""
    client.create_note(a["iid"], HARVEST_MARKER)
    if a["kind"] == "bug":
        return
    if a["status"] == "unanswered":
        client.update_issue(a["iid"], state_event="close", add_labels=[UNANSWERED], remove_labels=[mb.L_OPEN])
    elif a["kind"] == "question":
        client.update_issue(a["iid"], state_event="close", add_labels=[mb.L_ANSWERED], remove_labels=[mb.L_OPEN])
    else:
        client.update_issue(a["iid"], state_event="close")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Moltbook cold-archiver（GitLab CI）")
    p.add_argument("--repo-dir", default=".", help="Moltbook repo のチェックアウト先")
    p.add_argument("--dwell-hours", type=float, default=24.0, help="情報共有・バグ報告をファイル化するまでの時間")
    p.add_argument("--unanswered-days", type=float, default=14.0, help="答えの無い質問を未解決の一覧へ移すまでの日数")
    p.add_argument("--stale-days", type=float, default=90.0, help="点数 0 の投稿をヒントから外すまでの日数")
    p.add_argument("--min-goods", type=int, default=None, help="（廃止。指定しても使わない）")
    p.add_argument("--max", type=int, default=500)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.min_goods is not None:
        print("注意: --min-goods は廃止しました（👍 はファイル化の条件にしません）", file=sys.stderr)

    try:
        client = GitLabClient.from_ci_env(dry_run=args.dry_run)
        tally = run(client, Path(args.repo_dir), dwell_hours=args.dwell_hours,
                    unanswered_days=args.unanswered_days, stale_days=args.stale_days,
                    max_items=args.max, dry_run=args.dry_run)
    except GitLabError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print("集計: " + (", ".join(f"{k}={v}" for k, v in tally.items()) or "対象なし"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
