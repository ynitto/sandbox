#!/usr/bin/env python3
"""Read messages from a Microsoft Teams channel via Graph API."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["ChannelMessage.Read.All", "Team.ReadBasic.All", "Channel.ReadBasic.All", "offline_access"]


# ─── fuzzy name resolution ────────────────────────────────────────────────────

def _score(query: str, display_name: str) -> int:
    q, dn = query.lower(), display_name.lower()
    if dn == q:
        return 3
    if dn.startswith(q):
        return 2
    if q in dn:
        return 1
    return 0


def _select_from_matches(query: str, candidates: list[dict], label: str) -> dict:
    scored = sorted(
        [(c, _score(query, c["displayName"])) for c in candidates if _score(query, c["displayName"]) > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    if not scored:
        raise SystemExit(
            f"{label} '{query}' に一致する候補が見つかりません。"
            f"スペルを確認するか --{label.lower()}-id で ID を直接指定してください。"
        )

    hits = [item for item, _ in scored]

    if len(hits) == 1:
        if scored[0][1] == 3:
            print(f"{label}: 「{hits[0]['displayName']}」", file=sys.stderr)
            return hits[0]
        print(f"{label}候補: 「{hits[0]['displayName']}」", file=sys.stderr)
        while True:
            ans = input(f"この{label}のメッセージを読み取りますか？ [y/n]: ").strip().lower()
            if ans in ("y", "n"):
                break
        if ans != "y":
            raise SystemExit(f"キャンセルされました。--{label.lower()}-name を修正するか ID で指定してください。")
        return hits[0]

    print(f"'{query}' に一致する{label}が複数見つかりました:", file=sys.stderr)
    for i, item in enumerate(hits, 1):
        print(f"  [{i}] {item['displayName']}", file=sys.stderr)
    while True:
        raw = input(f"番号を選択してください [1-{len(hits)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(hits):
            return hits[int(raw) - 1]
        print(f"  1 から {len(hits)} の数字を入力してください。", file=sys.stderr)


# ─── Graph API helpers ────────────────────────────────────────────────────────

def _get(url: str, headers: dict, **kwargs) -> dict:
    resp = requests.get(url, headers=headers, **kwargs)
    resp.raise_for_status()
    return resp.json()


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text.strip()


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Microsoft Teams チャンネルのメッセージを読み取る")

    team_group = parser.add_mutually_exclusive_group(required=True)
    team_group.add_argument("--team-name", help="対象チームの表示名")
    team_group.add_argument("--team-id", help="対象チームの GUID")

    channel_group = parser.add_mutually_exclusive_group(required=True)
    channel_group.add_argument("--channel-name", help="対象チャンネルの表示名")
    channel_group.add_argument("--channel-id", help="対象チャンネルの ID")

    parser.add_argument("--top", type=int, default=20, help="取得件数（デフォルト 20、最大 50）")
    parser.add_argument("--filter-subject", help="タイトル（件名）で絞り込む（部分一致）")
    parser.add_argument("--show-body", action="store_true", help="本文プレビューを表示（先頭 100 文字）")
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON 形式で出力")

    args = parser.parse_args()

    token = get_token(SCOPES)
    headers = {"Authorization": f"Bearer {token}"}

    # チーム解決
    if args.team_id:
        team_id = args.team_id
        channel_display = args.channel_id or ""
    else:
        print(f"チーム '{args.team_name}' を検索中...", file=sys.stderr)
        teams = _get(f"{GRAPH_BASE}/me/joinedTeams", headers).get("value", [])
        team = _select_from_matches(args.team_name, teams, "チーム")
        team_id = team["id"]

    # チャンネル解決
    if args.channel_id:
        channel_id = args.channel_id
        channel_display = channel_id
    else:
        print(f"チャンネル '{args.channel_name}' を検索中...", file=sys.stderr)
        channels = _get(f"{GRAPH_BASE}/teams/{team_id}/channels", headers).get("value", [])
        channel = _select_from_matches(args.channel_name, channels, "チャンネル")
        channel_id = channel["id"]
        channel_display = channel["displayName"]

    # メッセージ取得
    top = min(args.top, 50)
    print(f"メッセージを取得中（最大 {top} 件）...", file=sys.stderr)
    messages = _get(
        f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages",
        headers,
        params={"$top": top},
    ).get("value", [])

    # フィルタリング
    if args.filter_subject:
        lower = args.filter_subject.lower()
        messages = [m for m in messages if m.get("subject") and lower in m["subject"].lower()]
        if not messages:
            print(f"タイトル '{args.filter_subject}' に一致するメッセージが見つかりません。")
            return

    if args.json_output:
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return

    if not messages:
        print("チャンネルにメッセージが見つかりません。")
        return

    print(f"\nチャンネル: {channel_display}  ({len(messages)} 件)")
    print("─" * 60)

    for msg in messages:
        ts = msg.get("createdDateTime", "")[:16].replace("T", " ")
        sender = (
            (msg.get("from") or {}).get("user", {}).get("displayName")
            or "(不明)"
        )
        msg_id = msg.get("id", "")
        subject = msg.get("subject", "")

        print(f"{ts}  {sender}  Id: {msg_id}")
        if subject:
            print(f"  件名: {subject}")
        if args.show_body:
            body_content = (msg.get("body") or {}).get("content", "")
            body_type = (msg.get("body") or {}).get("contentType", "text")
            plain = _strip_html(body_content) if body_type == "html" else body_content.strip()
            preview = plain[:100] + "…" if len(plain) > 100 else plain
            if preview:
                print(f"  本文: {preview}")
        print("─" * 60)

    print(f"合計 {len(messages)} 件を表示しました。")


if __name__ == "__main__":
    main()
