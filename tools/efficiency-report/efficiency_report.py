#!/usr/bin/env python3
"""集計済みの GitLab 効率メトリクスから月次レポートを生成する。"""

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


UNKNOWN = "未確認"
LEVELS = ("low", "mid", "high")
COUNTS = (
    ("マージ済み MR", "merged_mr_count"),
    ("差し戻し", "rework_count"),
    ("破棄 MR", "discarded_mr_count"),
)


def previous_month(today=None):
    today = today or datetime.now(timezone.utc).date()
    return (today.replace(day=1) - timedelta(days=1)).isoformat()[:7]


def check_month(month):
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000-"):
        raise ValueError("--month は YYYY-MM 形式で指定してください")
    return month


class JsonObject:
    """入力の型を確認し、エラーに JSON 内の位置を付ける。"""

    def __init__(self, value, path):
        if not isinstance(value, dict):
            raise ValueError(f"{path} は JSON オブジェクトである必要があります")
        self.value = value
        self.path = path

    def get(self, key, kind, *, required=False):
        path = f"{self.path}.{key}"
        if key not in self.value:
            if required:
                raise ValueError(f"{path} がありません")
            return None
        value = self.value[key]
        if kind is float:
            valid = type(value) is int or (type(value) is float and math.isfinite(value))
            label = "有限の数値"
        else:
            label, valid = {
                dict: ("オブジェクト", type(value) is dict),
                list: ("配列", type(value) is list),
                str: ("文字列", type(value) is str),
                bool: ("真偽値", type(value) is bool),
                int: ("非負整数", type(value) is int and value >= 0),
            }[kind]
        if not valid:
            raise ValueError(f"{path} は{label}である必要があります")
        return value

    def obj(self, key, *, required=False):
        return JsonObject(self.get(key, dict, required=required) or {}, f"{self.path}.{key}")

    def strings(self, key):
        values = self.get(key, list)
        if values is None:
            return UNKNOWN
        if any(not isinstance(value, str) for value in values):
            raise ValueError(f"{self.path}.{key} の要素は文字列である必要があります")
        return "、".join(markdown_text(value) for value in values) or "対象なし"


def read_object(filename, label):
    path = Path(filename).expanduser()
    try:
        with path.open(encoding="utf-8") as stream:
            return JsonObject(json.load(stream), label)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} ({path}) を読み込めません。{exc}") from exc


def markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]|@~])", r"\\\1", escape(" ".join(value.split())))


def number(value, unit=""):
    return UNKNOWN if value is None else f"{value:,}{unit}"


def range_cells(obj, unit=""):
    return [number(obj.get(level, float), unit) for level in LEVELS]


def issue_counts(time):
    return [time.obj(f"{kind}_actual").get("closed_issue_count", int) for kind in ("ai", "human")]


def closed_total(time):
    counts = issue_counts(time)
    return sum(counts) if None not in counts else None


def table(lines, headers, rows):
    lines.extend(["", "| " + " | ".join(headers) + " |",
                  "| " + " | ".join("---" for _ in headers) + " |"])
    lines.extend("| " + " | ".join(row) + " |" for row in rows)


def append_breakdown(lines, source, axis, title):
    groups = source.obj(axis)
    lines.extend(["", f"## {title}"])
    if not groups.value:
        lines.extend(["", UNKNOWN if axis not in source.value else "対象なし"])
        return []
    rows, candidates = [], []
    is_repo = axis == "by_repo"
    for name in sorted(groups.value):
        group = groups.obj(name)
        raw = group.obj("raw_metrics")
        estimate = group.obj("estimate")
        currency = estimate.get("currency", str) or UNKNOWN
        merged, rework, discarded = [raw.get(key, int) for _, key in COUNTS]
        row = [markdown_text(name)]
        if is_repo:
            row.extend(number(value, " 件") for value in (
                closed_total(group.obj("time")), merged, rework, discarded
            ))
            if rework:
                candidates.append((rework, name))
        else:
            row.extend([group.strings("kinds"), number(merged, " 件"),
                        number(raw.get("deliverable_bytes", int), " B")])
        rows.append(row + range_cells(estimate.obj("savings"), f" {markdown_text(currency)}"))
    headers = (["リポジトリ", "閉じた Issue", "マージ済み MR", "差し戻し", "破棄 MR"]
               if is_repo else ["ユーザー", "種別", "マージ済み MR", "成果物"])
    table(lines, headers + [f"削減額 {level}" for level in LEVELS], rows)
    if not is_repo:
        lines.extend(["", "ユーザー別の削減額には差し戻し分のコストを含めていません。"])
    return candidates


def append_assumptions(lines, source, currency):
    params = source.obj("params")
    classification = source.obj("classification")
    lines.extend([
        "", "## 前提", "",
        f"- 人件費 {number(params.get('human_hourly_rate', float), f' {currency}/h')}",
        "- 実装スループット（低 / 中 / 高） " + " / ".join(
            number(params.get(f"write_loc_per_day_{level}", float), " LOC/人日") for level in LEVELS
        ),
        f"- レビュー速度 {number(params.get('review_bytes_per_minute', float), ' B/分')}",
        f"- AI 判定の対象ユーザー {classification.strings('agent_users')}",
        f"- AI 判定の本文マーカー（正規表現） {classification.strings('ai_markers')}",
        "",
        "収集時は本文マーカーまたは対象ユーザーで AI 種別を判定します。"
        "マーカーの欠落やアカウントの共有により、実際の AI 利用と分類がずれる場合があります。",
        "削減推定の対象は AI 種別です。成果物バイト数には自動生成物なども含まれ得ます。",
    ])


def append_audit(lines, audit, month):
    if audit is None:
        lines.extend(["", "エージェント利用・実行品質（agent-audit）は未集計です。"])
        return

    if "usage" in audit.value:
        if audit.get("month", str, required=True) != month:
            raise ValueError("audit_input.month が対象月と一致しません")
        usage = audit.obj("usage", required=True)
        stats = audit.obj("stats") if "stats" in audit.value else None
        period_note = f"集計対象月 {month}（入力の申告）"
    else:
        usage, stats = audit, None
        period_note = "集計対象月は未確認です。usage の month は収集時の UTC 当月を表します。"

    if usage.get("period", str, required=True) != "month" or usage.get("by", str, required=True) != "agent_cli":
        raise ValueError(f"{usage.path} には agent-audit usage --period month --by agent_cli --json の結果を指定してください")
    lines.extend(["", "## エージェント利用・実行品質（agent-audit）", "", period_note])
    use_rows = []
    for index, value in enumerate(usage.get("rows", list, required=True)):
        row = JsonObject(value, f"{usage.path}.rows[{index}]")
        use_rows.append([markdown_text(row.get("group", str, required=True))] + [
            number(row.get(key, int, required=True)) for key in
            ("measured_in", "measured_out", "estimated_tokens", "unmeasured_runs")
        ])
    if use_rows:
        table(lines, ["CLI", "実測入力", "実測出力", "推定トークン", "未計測実行"], use_rows)
    else:
        lines.extend(["", "トークン使用量は対象なしです。"])

    if stats is None:
        lines.extend(["", "失敗クラスは未集計です。"])
        return
    if stats.get("period", str, required=True) != "month":
        raise ValueError(f"{stats.path}.period には month を指定してください")
    failure_rows = []
    for index, value in enumerate(stats.get("tools", list, required=True)):
        row = JsonObject(value, f"{stats.path}.tools[{index}]")
        tool = markdown_text(row.get("tool", str, required=True))
        errors = row.obj("error_class", required=True)
        failure_rows.extend([tool, markdown_text(key), number(errors.get(key, int, required=True))]
                            for key in sorted(errors.value))
    if failure_rows:
        table(lines, ["ツール", "失敗クラス", "件数"], failure_rows)
    else:
        lines.extend(["", "失敗クラスは対象なしです。"])


def render_report(source, month, audit=None):
    totals = source.obj("totals", required=True)
    raw = totals.obj("raw_metrics", required=True)
    time = totals.obj("time")
    estimate = totals.obj("estimate")
    window = source.obj("window")
    since = window.get("since", str) or UNKNOWN
    until = window.get("until", str) or UNKNOWN
    currency = markdown_text(estimate.get("currency", str) or UNKNOWN)
    lines = [
        f"[efficiency-report: {month}]", "", "# エージェント活用の月次効率レポート", "",
        f"- 対象月 {month}",
        f"- 入力の集計期間 {markdown_text(since)} ～ {markdown_text(until)}",
        f"- 対象リポジトリ {source.strings('repos')}",
        "", "## 処理件数", "",
        f"- 閉じた Issue {number(closed_total(time), ' 件')}",
    ]
    lines.extend(f"- {label} {number(raw.get(key, int), ' 件')}" for label, key in COUNTS)
    lines.extend(["", "## リードタイム", ""])
    lead_rows = []
    for kind, label in (("ai", "AI"), ("human", "人")):
        actual = time.obj(f"{kind}_actual")
        closed = actual.get("closed_issue_count", int)
        started = actual.get("active_counted", int)
        cycle = actual.get("avg_cycle_time_hours", float)
        active = actual.get("avg_active_time_hours", float)
        lead_rows.append([
            label, number(closed, " 件"),
            "対象なし" if closed == 0 else number(cycle, " h"),
            number(started, " 件"), "対象なし" if started == 0 else number(active, " h"),
        ])
    table(lines, ["種別", "閉じた Issue", "平均サイクルタイム（起票→クローズ）",
                  "着手時刻あり", "平均正味時間（着手→クローズ）"], lead_rows)
    lines.extend(["", "## 推定削減"])
    table(lines, ["指標", *LEVELS], [
        ["削減コスト", *range_cells(estimate.obj("savings"), f" {currency}")],
        ["削減時間（総）", *range_cells(time.obj("time_saved_days"), " 日")],
        ["削減時間（正味）", *range_cells(time.obj("time_saved_net_days"), " 日")],
    ])
    lines.extend(["", "low / mid / high は人の生産性を変えた 3 シナリオです。"
                  "削減日数は人の実働日の見積もりと AI の実時間を比較した概算です。"])
    candidates = append_breakdown(lines, source, "by_repo", "リポジトリ別内訳")
    append_breakdown(lines, source, "by_user", "ユーザー別内訳")
    append_assumptions(lines, source, currency)
    append_audit(lines, audit, month)
    lines.extend(["", "## 改善施策への示唆", ""])
    if candidates:
        count, name = max(candidates)
        lines.append(f"- 内訳で差し戻しが最多の {markdown_text(name)}（{count:,} 回）について、"
                     "処理量と差し戻し理由を確認すると改善の優先付けに使えます。")
    discarded = raw.get("discarded_mr_count", int)
    if discarded:
        lines.append(f"- 破棄 MR は {discarded:,} 件です。探索や学習の結果もあり得るため、"
                     "クローズ理由を確認してから改善対象を判断します。")
    if not candidates and not discarded:
        lines.append("- 差し戻しのリポジトリ別内訳と破棄 MR の件数から、優先候補を挙げる材料は得られませんでした。")
    return "\n".join(lines) + "\n"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def notes_endpoint(issue_url):
    hub = urlsplit(issue_url)
    match = re.fullmatch(r"/(.+)/-/issues/([1-9][0-9]*)/?", hub.path)
    if (hub.scheme != "https" or not hub.hostname or hub.username is not None
            or hub.query or hub.fragment or any(c.isspace() for c in issue_url) or not match):
        raise ValueError("hub_issue_url には GitLab の HTTPS issue URL を指定してください")
    project, iid = match.groups()
    parts = [unquote(part) for part in project.split("/")]
    if len(parts) < 2 or any(
        part in (".", "..") or not re.fullmatch(r"[\w.-]+", part) for part in parts
    ):
        raise ValueError("hub_issue_url のプロジェクトパスが不正です")
    return f"https://{hub.netloc}/api/v4/projects/{quote('/'.join(parts), safe='')}/issues/{iid}/notes"


def post_report(report, issue_url, token):
    endpoint = notes_endpoint(issue_url)
    if not token:
        raise ValueError("GITLAB_TOKEN を設定してください")
    opener = build_opener(NoRedirect())
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    marker = report.splitlines()[0]
    page = 1
    while True:
        query = urlencode({"per_page": 100, "page": page})
        with opener.open(Request(f"{endpoint}?{query}", headers=headers), timeout=30) as response:
            notes = json.load(response)
            next_page = response.headers.get("X-Next-Page")
        if not isinstance(notes, list) or any(
            not isinstance(note, dict) or not isinstance(note.get("body"), str) for note in notes
        ):
            raise ValueError("GitLab のコメント一覧の形式が不正です")
        if any(note["body"].partition("\n")[0].rstrip("\r") == marker for note in notes):
            return False
        if next_page == "" or (next_page is None and len(notes) < 100):
            break
        following = int(next_page) if next_page is not None else page + 1
        if following <= page:
            raise ValueError("GitLab の次ページ番号が不正です")
        page = following
    data = json.dumps({"body": report}, ensure_ascii=False).encode("utf-8")
    with opener.open(Request(endpoint, headers=headers, data=data), timeout=30):
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="JSON 設定ファイル")
    parser.add_argument("--input", help="efficiency.py collect の JSON 出力")
    parser.add_argument("--audit-input", help="agent-audit の集計 JSON")
    parser.add_argument("--month", help="対象月 YYYY-MM（既定は UTC の前月）")
    parser.add_argument("--hub-issue-url", help="GitLab ハブ issue の URL")
    parser.add_argument("--post", action="store_true", help="ハブ issue に投稿する")
    parser.add_argument("--dry-run", action="store_true", help="設定や --post にかかわらず投稿しない")
    args = parser.parse_args(argv)

    try:
        config = read_object(args.config, "config") if args.config else JsonObject({}, "config")
        month = check_month(args.month if args.month is not None else previous_month())
        filename = args.input or config.get("input", str)
        if not filename:
            raise ValueError("--input または config.input を指定してください")
        audit_file = args.audit_input or config.get("audit_input", str)
        audit = read_object(audit_file, "audit_input") if audit_file else None
        posting = not args.dry_run and (args.post or config.get("post_enabled", bool))
        report = render_report(read_object(filename, "input"), month, audit)
        print(report, end="")
        if not posting:
            print("dry-run（投稿なし）", file=sys.stderr)
            return 0

        issue_url = args.hub_issue_url or config.get("hub_issue_url", str) or os.getenv("GITLAB_HUB_ISSUE_URL", "")
        posted = post_report(report, issue_url, os.getenv("GITLAB_TOKEN", "").strip())
        print("投稿しました" if posted else "同じ対象月の投稿があるためスキップしました", file=sys.stderr)
    except HTTPError as exc:
        exc.close()
        print(f"投稿処理エラー（GitLab HTTP {exc.code}）", file=sys.stderr)
        return 2
    except (OSError, HTTPException, ValueError) as exc:
        print(f"エラー（{exc}）", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
