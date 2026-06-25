#!/usr/bin/env python3
"""
efficiency.py - GitLab のイシュー / MR データから「エージェント活用による効率（コスト削減）」を推定する。

生メトリクス（GitLab から収集）:
  - deliverable_bytes  : マージ済み MR の差分で追加された行のバイト数（= 成果物として生成したコード量）
  - agent_comment_bytes: イシュー / MR にエージェントが投稿したコメントのバイト数
  - rework_count       : 差し戻し回数（イシューの reopen + needs-rework ラベル付与）
  - discarded_mr_count : 破棄した MR の数（マージされずにクローズされた MR）
  - review_minutes_est : 推定レビュー時間（MR の変更量から見積もり）

コストモデル（references/cost-model.md 参照）:
  人手のみで作った場合のコスト（counterfactual）と、エージェント利用時の実コストを比較し、
  PERT 風の楽観 / 最頻 / 悲観レンジで削減額・削減率を出す。前提パラメータはすべて上書き可能。

依存:
  GitLab 接続・認証は同梱リポジトリ内の gitlab-idd スキルの gl.py を再利用する
  （../../gitlab-idd/scripts/gl.py）。GITLAB_TOKEN とリポジトリの git remote / connections.yaml が必要。

使い方:
  python efficiency.py --days 30
  python efficiency.py --since 2026-05-01 --until 2026-06-01 --agent-users alice,bob
  python efficiency.py --days 30 --params-file params.json --format markdown
  python efficiency.py --days 30 --get savings.mid   # 単一フィールド抽出

Python 3.8+ / stdlib のみ。
"""

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# gitlab-idd の gl.py を動的ロードして接続・API ヘルパを再利用する
# ---------------------------------------------------------------------------

def _load_gl():
    """同じ .github/skills/ 配下にある gitlab-idd/scripts/gl.py を import する。"""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent.parent / "gitlab-idd" / "scripts" / "gl.py",   # 標準配置
        here.parent / "gl.py",                                       # 同梱した場合
    ]
    gl_path = next((p for p in candidates if p.exists()), None)
    if gl_path is None:
        sys.exit(
            "ERROR: gitlab-idd の gl.py が見つかりません。\n"
            "  このスキルは GitLab 接続のために gitlab-idd スキルを同じ skills ディレクトリに必要とします。\n"
            f"  探索した場所: {[str(c) for c in candidates]}"
        )
    spec = importlib.util.spec_from_file_location("_gl", str(gl_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GL = _load_gl()


# ---------------------------------------------------------------------------
# 既定パラメータ（references/cost-model.md に解説）。すべて上書き可能。
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "currency": "JPY",
    "human_hourly_rate": 6000,          # エンジニア 1 時間あたりの人件費

    # 人手での実装スループット（LOC/人日）。楽観=生産性が高い→人コスト小→削減小。
    "productive_hours_per_day": 6,      # 1 人日あたりの正味実装時間
    "bytes_per_loc": 40,                # 1 行あたりの平均バイト数（成果物バイト→行数換算）
    "write_loc_per_day_low": 30,        # 悲観（生産性低い）
    "write_loc_per_day_mid": 60,        # 最頻
    "write_loc_per_day_high": 120,      # 楽観（生産性高い）

    # レビュー（人）の見積もり
    "review_bytes_per_minute": 270,     # レビュー速度（≒ 400 LOC/h）
    "review_fixed_min_per_mr": 5,       # MR 1 件あたりの固定オーバーヘッド
    "review_cap_min_per_mr": 120,       # MR 1 件あたりのレビュー時間の上限

    # エージェント利用時に人側へ発生する追加オーバーヘッド
    "per_rework_human_min": 20,         # 差し戻し 1 回あたりの人の追加時間（再レビュー・指摘）
    "per_discard_human_min": 15,        # 破棄 MR 1 件あたりの人の追加時間（確認・判断）

    # エージェントの計算コスト（出力トークン課金の概算）。実請求があれば agent_cost_override で上書き。
    "bytes_per_token": 4,               # バイト→トークン概算
    "rework_redo_factor": 1.3,          # 差し戻しによる再生成ぶんの係数
    "price_per_1k_output_tokens": 0,    # 1k 出力トークンあたりの単価（0=計算コストを無視）
    "agent_cost_override": None,        # 指定すると計算コストをこの固定値にする
}

# 差し戻しとみなすイシューラベル
DEFAULT_REWORK_LABELS = ["status:needs-rework"]


# ---------------------------------------------------------------------------
# 収集ヘルパ
# ---------------------------------------------------------------------------

def _to_utc_iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(s: str) -> datetime:
    """YYYY-MM-DD または ISO8601 を UTC aware datetime に。"""
    dt = GL._parse_iso8601_utc(s if "T" in s else s + "T00:00:00+00:00")
    if dt is None:
        sys.exit(f"ERROR: 日付を解釈できません: {s}（YYYY-MM-DD 形式で指定してください）")
    return dt


def _in_window(ts, since, until) -> bool:
    dt = GL._parse_iso8601_utc(ts) if isinstance(ts, str) else ts
    if dt is None:
        return False
    return since <= dt <= until


def _diff_added_bytes(diff_text: str):
    """unified diff から追加行のバイト数・行数を数える（'+++' ヘッダは除外）。"""
    added_bytes = 0
    added_lines = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_bytes += len(line[1:].encode("utf-8"))
            added_lines += 1
    return added_bytes, added_lines


def collect(host, token, project, since, until, agent_users, rework_labels, verbose=False):
    """GitLab から生メトリクスを収集して dict で返す。"""
    ep = GL.encode_project(project)
    agent_set = set(agent_users)
    since_iso = _to_utc_iso(since)

    def log(msg):
        if verbose:
            print(f"  [collect] {msg}", file=sys.stderr)

    # --- Merge Requests ---------------------------------------------------
    mrs = GL.api_list(host, token, f"/projects/{ep}/merge_requests",
                      params={"state": "all", "updated_after": since_iso, "scope": "all"})
    log(f"MR 候補 {len(mrs)} 件")

    deliverable_bytes = 0
    deliverable_lines = 0
    merged_mr_count = 0
    discarded_mr_count = 0
    review_minutes_est = 0.0
    mr_comment_bytes = 0
    reviewed_mr_iids = []

    for mr in mrs:
        iid = mr.get("iid")
        author = (mr.get("author") or {}).get("username", "")
        is_agent = author in agent_set
        state = mr.get("state")
        merged_at = mr.get("merged_at")
        closed_at = mr.get("closed_at")

        # 成果物（マージ済み・エージェント作成・期間内）
        if state == "merged" and is_agent and _in_window(merged_at, since, until):
            changes = GL.api(host, token, "GET",
                             f"/projects/{ep}/merge_requests/{iid}/changes")
            added_b = 0
            added_l = 0
            for ch in changes.get("changes", []):
                b, l = _diff_added_bytes(ch.get("diff", ""))
                added_b += b
                added_l += l
            deliverable_bytes += added_b
            deliverable_lines += added_l
            merged_mr_count += 1
            # 推定レビュー時間（変更量ベース）
            rmin = added_b / max(1, params_review_bpm) + params_review_fixed
            review_minutes_est += min(rmin, params_review_cap)
            reviewed_mr_iids.append(iid)
            log(f"MR !{iid} merged: +{added_b}B / +{added_l}行")

        # 破棄（マージされずクローズ・期間内）
        if state == "closed" and not merged_at and _in_window(closed_at, since, until):
            discarded_mr_count += 1
            log(f"MR !{iid} discarded")

        # MR コメント（エージェント投稿・期間内）
        notes = GL.api_list(host, token, f"/projects/{ep}/merge_requests/{iid}/notes")
        for n in notes:
            if n.get("system"):
                continue
            if (n.get("author") or {}).get("username", "") not in agent_set:
                continue
            if not _in_window(n.get("created_at"), since, until):
                continue
            mr_comment_bytes += len((n.get("body") or "").encode("utf-8"))

    # --- Issues -----------------------------------------------------------
    issues = GL.api_list(host, token, f"/projects/{ep}/issues",
                         params={"state": "all", "updated_after": since_iso, "scope": "all"})
    log(f"イシュー候補 {len(issues)} 件")

    issue_comment_bytes = 0
    rework_count = 0
    rework_label_set = set(rework_labels)

    for issue in issues:
        iid = issue.get("iid")

        # エージェント投稿コメント
        notes = GL.api_list(host, token, f"/projects/{ep}/issues/{iid}/notes")
        for n in notes:
            if n.get("system"):
                continue
            if (n.get("author") or {}).get("username", "") not in agent_set:
                continue
            if not _in_window(n.get("created_at"), since, until):
                continue
            issue_comment_bytes += len((n.get("body") or "").encode("utf-8"))

        # 差し戻し: reopen 状態イベント
        state_events = GL.api_list(
            host, token, f"/projects/{ep}/issues/{iid}/resource_state_events")
        for ev in state_events:
            if ev.get("state") == "reopened" and _in_window(ev.get("created_at"), since, until):
                rework_count += 1

        # 差し戻し: needs-rework ラベル付与イベント
        label_events = GL.api_list(
            host, token, f"/projects/{ep}/issues/{iid}/resource_label_events")
        for ev in label_events:
            if ev.get("action") != "add":
                continue
            label_name = (ev.get("label") or {}).get("name", "")
            if label_name in rework_label_set and _in_window(ev.get("created_at"), since, until):
                rework_count += 1

    agent_comment_bytes = issue_comment_bytes + mr_comment_bytes

    return {
        "window": {"since": _to_utc_iso(since), "until": _to_utc_iso(until)},
        "agent_users": sorted(agent_set),
        "raw_metrics": {
            "deliverable_bytes": deliverable_bytes,
            "deliverable_lines": deliverable_lines,
            "merged_mr_count": merged_mr_count,
            "discarded_mr_count": discarded_mr_count,
            "rework_count": rework_count,
            "agent_comment_bytes": agent_comment_bytes,
            "issue_comment_bytes": issue_comment_bytes,
            "mr_comment_bytes": mr_comment_bytes,
            "review_minutes_est": round(review_minutes_est, 1),
            "reviewed_mr_count": len(reviewed_mr_iids),
        },
    }


# レビュー見積もり用のモジュールグローバル（collect から参照）
params_review_bpm = DEFAULT_PARAMS["review_bytes_per_minute"]
params_review_fixed = DEFAULT_PARAMS["review_fixed_min_per_mr"]
params_review_cap = DEFAULT_PARAMS["review_cap_min_per_mr"]


# ---------------------------------------------------------------------------
# コスト計算
# ---------------------------------------------------------------------------

def _scenario_cost(deliverable_bytes, review_minutes, rework_count,
                   discarded_mr_count, agent_comment_bytes, p, loc_per_day):
    """1 つの生産性前提でのコスト内訳を返す。"""
    rate = p["human_hourly_rate"]
    write_bytes_per_hour = (loc_per_day * p["bytes_per_loc"]) / p["productive_hours_per_day"]
    author_hours = deliverable_bytes / write_bytes_per_hour if write_bytes_per_hour else 0.0
    review_hours = review_minutes / 60.0

    # 人手のみで作った場合（counterfactual）: 実装 + レビュー
    human_authoring_cost = author_hours * rate
    human_review_cost = review_hours * rate
    human_only_cost = human_authoring_cost + human_review_cost

    # エージェント利用時の追加オーバーヘッド（人側）
    rework_overhead_cost = (rework_count * p["per_rework_human_min"] / 60.0) * rate
    discard_overhead_cost = (discarded_mr_count * p["per_discard_human_min"] / 60.0) * rate

    # エージェント計算コスト
    if p["agent_cost_override"] is not None:
        agent_compute_cost = p["agent_cost_override"]
    else:
        gen_bytes = (deliverable_bytes + agent_comment_bytes) * p["rework_redo_factor"]
        tokens = gen_bytes / p["bytes_per_token"]
        agent_compute_cost = tokens / 1000.0 * p["price_per_1k_output_tokens"]

    # エージェント利用時の総コスト: 計算コスト + 人のレビュー + 追加オーバーヘッド
    # （レビューは両シナリオに共通。実装ぶんがエージェントに置き換わる）
    agent_scenario_cost = (agent_compute_cost + human_review_cost
                           + rework_overhead_cost + discard_overhead_cost)

    savings = human_only_cost - agent_scenario_cost
    savings_pct = (savings / human_only_cost * 100.0) if human_only_cost else 0.0
    human_hours_saved = author_hours  # 実装ぶんがまるごと人手から消える

    return {
        "loc_per_day_assumed": loc_per_day,
        "author_hours": round(author_hours, 2),
        "review_hours": round(review_hours, 2),
        "human_only_cost": round(human_only_cost),
        "agent_scenario_cost": round(agent_scenario_cost),
        "breakdown": {
            "human_authoring_cost": round(human_authoring_cost),
            "human_review_cost": round(human_review_cost),
            "agent_compute_cost": round(agent_compute_cost),
            "rework_overhead_cost": round(rework_overhead_cost),
            "discard_overhead_cost": round(discard_overhead_cost),
        },
        "savings": round(savings),
        "savings_pct": round(savings_pct, 1),
        "human_hours_saved": round(human_hours_saved, 2),
    }


def estimate(raw, p):
    rm = raw["raw_metrics"]
    args_common = dict(
        deliverable_bytes=rm["deliverable_bytes"],
        review_minutes=rm["review_minutes_est"],
        rework_count=rm["rework_count"],
        discarded_mr_count=rm["discarded_mr_count"],
        agent_comment_bytes=rm["agent_comment_bytes"],
        p=p,
    )
    # 楽観 = 人の生産性が高い = 削減が小さい / 悲観 = 生産性が低い = 削減が大きい
    high = _scenario_cost(loc_per_day=p["write_loc_per_day_high"], **args_common)  # 削減 低
    mid = _scenario_cost(loc_per_day=p["write_loc_per_day_mid"], **args_common)
    low = _scenario_cost(loc_per_day=p["write_loc_per_day_low"], **args_common)   # 削減 高
    return {
        "currency": p["currency"],
        "savings": {"low": high["savings"], "mid": mid["savings"], "high": low["savings"]},
        "savings_pct": {"low": high["savings_pct"], "mid": mid["savings_pct"], "high": low["savings_pct"]},
        "human_hours_saved": {"low": high["human_hours_saved"], "mid": mid["human_hours_saved"], "high": low["human_hours_saved"]},
        "scenarios": {"optimistic": high, "most_likely": mid, "pessimistic": low},
        "assumptions": p,
    }


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def render_markdown(result):
    rm = result["raw_metrics"]
    est = result["estimate"]
    cur = est["currency"]
    w = result["window"]
    s = est["savings"]
    sp = est["savings_pct"]
    hh = est["human_hours_saved"]

    def money(v):
        return f"{v:,} {cur}"

    lines = [
        "# GitLab 効率性メトリクス レポート",
        "",
        f"- 対象期間: {w['since']} 〜 {w['until']}",
        f"- エージェントユーザー: {', '.join(result['agent_users'])}",
        "",
        "## 生メトリクス",
        "",
        "| 指標 | 値 |",
        "|------|----|",
        f"| 成果物バイト数（マージ済み MR の追加行） | {rm['deliverable_bytes']:,} B（{rm['deliverable_lines']:,} 行 / {rm['merged_mr_count']} MR） |",
        f"| エージェント投稿バイト数（イシュー+MR コメント） | {rm['agent_comment_bytes']:,} B（イシュー {rm['issue_comment_bytes']:,} / MR {rm['mr_comment_bytes']:,}） |",
        f"| 差し戻し回数（reopen + needs-rework） | {rm['rework_count']} 回 |",
        f"| 破棄した MR | {rm['discarded_mr_count']} 件 |",
        f"| 推定レビュー時間 | {rm['review_minutes_est']:.0f} 分（{rm['reviewed_mr_count']} MR） |",
        "",
        "## コスト削減の推定（レンジ）",
        "",
        "| | 楽観（削減小） | 最頻 | 悲観（削減大） |",
        "|------|------|------|------|",
        f"| 削減額 | {money(s['low'])} | {money(s['mid'])} | {money(s['high'])} |",
        f"| 削減率 | {sp['low']}% | {sp['mid']}% | {sp['high']}% |",
        f"| 削減人時 | {hh['low']} h | {hh['mid']} h | {hh['high']} h |",
        "",
        "## 最頻シナリオの内訳",
        "",
    ]
    ml = est["scenarios"]["most_likely"]
    bd = ml["breakdown"]
    lines += [
        "| 項目 | 金額 |",
        "|------|------|",
        f"| 人手のみの総コスト | {money(ml['human_only_cost'])} |",
        f"| ├ 実装（人） | {money(bd['human_authoring_cost'])} |",
        f"| └ レビュー（人） | {money(bd['human_review_cost'])} |",
        f"| エージェント利用時の総コスト | {money(ml['agent_scenario_cost'])} |",
        f"| ├ エージェント計算コスト | {money(bd['agent_compute_cost'])} |",
        f"| ├ レビュー（人・共通） | {money(bd['human_review_cost'])} |",
        f"| ├ 差し戻し対応（人） | {money(bd['rework_overhead_cost'])} |",
        f"| └ 破棄 MR 対応（人） | {money(bd['discard_overhead_cost'])} |",
        f"| **削減額** | **{money(ml['savings'])}（{ml['savings_pct']}%）** |",
        "",
        "## 前提",
        "",
        f"- 人件費: {est['assumptions']['human_hourly_rate']:,} {cur}/h",
        f"- 実装スループット（LOC/人日）: 楽観 {est['assumptions']['write_loc_per_day_high']} / 最頻 {est['assumptions']['write_loc_per_day_mid']} / 悲観 {est['assumptions']['write_loc_per_day_low']}",
        f"- 1 行あたりバイト数: {est['assumptions']['bytes_per_loc']} B、正味実装時間: {est['assumptions']['productive_hours_per_day']} h/人日",
        f"- レビュー速度: {est['assumptions']['review_bytes_per_minute']} B/分、差し戻し {est['assumptions']['per_rework_human_min']} 分/回、破棄 {est['assumptions']['per_discard_human_min']} 分/件",
        "",
        "> 前提値は概算のデフォルト。自組織の実績値に合わせて --params-file で上書きすること。",
    ]
    return "\n".join(lines)


def _extract(obj, field_path):
    cur = obj
    for part in field_path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            sys.exit(f"ERROR: '{part}' を辿れません")
    return cur


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="直近 N 日を対象（--since 未指定時、既定 30）")
    ap.add_argument("--since", help="集計開始日 YYYY-MM-DD（指定時は --days より優先）")
    ap.add_argument("--until", help="集計終了日 YYYY-MM-DD（既定: 現在）")
    ap.add_argument("--agent-users", help="エージェントの GitLab username（カンマ区切り、既定: 認証ユーザー）")
    ap.add_argument("--rework-labels", default=",".join(DEFAULT_REWORK_LABELS),
                    help="差し戻しとみなすラベル（カンマ区切り）")
    ap.add_argument("--params-file", help="コスト前提を上書きする JSON ファイル")
    ap.add_argument("--label-conn", default="default", help="connections.yaml の接続ラベル")
    ap.add_argument("--format", choices=["json", "markdown"], default="json")
    ap.add_argument("--get", help="JSON 出力から単一フィールドを抽出（例: estimate.savings.mid）")
    ap.add_argument("--verbose", action="store_true", help="収集過程を stderr に出力")
    args = ap.parse_args()

    # 接続
    host, project = GL.get_project_info(args.label_conn)
    token = GL.get_token(args.label_conn)

    # 期間
    until = _parse_date(args.until) if args.until else datetime.now(timezone.utc)
    if args.since:
        since = _parse_date(args.since)
    else:
        since = until - timedelta(days=args.days)

    # エージェントユーザー
    if args.agent_users:
        agent_users = [u.strip() for u in args.agent_users.split(",") if u.strip()]
    else:
        me = GL.api(host, token, "GET", "/user")
        agent_users = [me.get("username", "")]

    rework_labels = [l.strip() for l in args.rework_labels.split(",") if l.strip()]

    # パラメータ
    params = dict(DEFAULT_PARAMS)
    if args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            params.update(json.load(f))

    # レビュー見積もり用グローバルを反映
    global params_review_bpm, params_review_fixed, params_review_cap
    params_review_bpm = params["review_bytes_per_minute"]
    params_review_fixed = params["review_fixed_min_per_mr"]
    params_review_cap = params["review_cap_min_per_mr"]

    raw = collect(host, token, project, since, until, agent_users, rework_labels, args.verbose)
    est = estimate(raw, params)
    result = {**raw, "estimate": est}

    if args.get:
        val = _extract(result, args.get)
        print(val if isinstance(val, (str, int, float, bool)) else json.dumps(val, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
