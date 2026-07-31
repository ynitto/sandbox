#!/usr/bin/env python3
"""Send a message to a Microsoft Teams channel via Graph API."""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
BASE_SCOPES = [
    "ChannelMessage.Send",
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "offline_access",
]
READ_SCOPE = "ChannelMessage.Read.All"


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
            ans = input(f"この{label}に投稿しますか？ [y/n]: ").strip().lower()
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


def _resolve_team(headers: dict, team_name: str | None, team_id: str | None) -> tuple[str, str]:
    """Return (team_id, team_display_name)."""
    if team_id:
        data = _get(f"{GRAPH_BASE}/teams/{team_id}", headers)
        return team_id, data.get("displayName", team_id)
    teams = _get(f"{GRAPH_BASE}/me/joinedTeams", headers).get("value", [])
    team = _select_from_matches(team_name, teams, "チーム")
    return team["id"], team["displayName"]


def _resolve_channel(
    headers: dict, team_id: str, channel_name: str | None, channel_id: str | None
) -> tuple[str, str, str]:
    """Return (channel_id, channel_display_name, membership_type)."""
    if channel_id:
        data = _get(f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}", headers)
        return channel_id, data.get("displayName", channel_id), data.get("membershipType", "standard")
    channels = _get(f"{GRAPH_BASE}/teams/{team_id}/channels", headers).get("value", [])
    ch = _select_from_matches(channel_name, channels, "チャンネル")
    return ch["id"], ch["displayName"], ch.get("membershipType", "standard")


def _find_message_by_subject(headers: dict, team_id: str, channel_id: str, subject: str) -> str:
    """Return message_id whose subject best matches the query."""
    messages = _get(
        f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages",
        headers,
        params={"$top": 50},
    ).get("value", [])

    with_subject = [m for m in messages if m.get("subject")]
    if not with_subject:
        raise SystemExit("チャンネル内にタイトル付きメッセージが見つかりません。")

    scored = sorted(
        [(m, _score(subject, m["subject"])) for m in with_subject if _score(subject, m["subject"]) > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    if not scored:
        raise SystemExit(f"タイトル '{subject}' に一致するメッセージが見つかりません。")

    hits = [item for item, _ in scored]

    if len(hits) == 1:
        if scored[0][1] == 3:
            ts = hits[0].get("createdDateTime", "")[:16].replace("T", " ")
            print(f"メッセージ: 「{hits[0]['subject']}」 ({ts})", file=sys.stderr)
            return hits[0]["id"]
        ts = hits[0].get("createdDateTime", "")[:16].replace("T", " ")
        print(f"メッセージ候補: 「{hits[0]['subject']}」 ({ts})", file=sys.stderr)
        while True:
            ans = input("このメッセージに返信しますか？ [y/n]: ").strip().lower()
            if ans in ("y", "n"):
                break
        if ans != "y":
            raise SystemExit("キャンセルされました。キーワードを変更するか --reply-to-message-id で指定してください。")
        return hits[0]["id"]

    print(f"'{subject}' に一致するメッセージが複数見つかりました:", file=sys.stderr)
    for i, m in enumerate(hits, 1):
        ts = m.get("createdDateTime", "")[:16].replace("T", " ")
        print(f"  [{i}] 「{m['subject']}」 ({ts})", file=sys.stderr)
    while True:
        raw = input(f"番号を選択してください [1-{len(hits)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(hits):
            return hits[int(raw) - 1]["id"]
        print(f"  1 から {len(hits)} の数字を入力してください。", file=sys.stderr)


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Microsoft Teams チャンネルにメッセージを投稿する")

    team_group = parser.add_mutually_exclusive_group(required=True)
    team_group.add_argument("--team-name", help="投稿先チームの表示名")
    team_group.add_argument("--team-id", help="投稿先チームの GUID")

    channel_group = parser.add_mutually_exclusive_group(required=True)
    channel_group.add_argument("--channel-name", help="投稿先チャンネルの表示名")
    channel_group.add_argument("--channel-id", help="投稿先チャンネルの ID")

    parser.add_argument("--message", required=True, help="投稿するメッセージ本文")
    parser.add_argument("--subject", help="メッセージのタイトル（件名）。返信時は無視される")
    parser.add_argument(
        "--content-type", choices=["text", "html"], default="text",
        help="本文形式（デフォルト: text）。--mention-* 指定時は自動的に html になる",
    )
    parser.add_argument("--mention-channel", action="store_true", help="@channel メンションを付ける")
    parser.add_argument("--mention-team", action="store_true", help="@team メンションを付ける")

    reply_group = parser.add_mutually_exclusive_group()
    reply_group.add_argument("--reply-to-message-id", help="返信先メッセージの ID（スレッド返信）")
    reply_group.add_argument(
        "--reply-to-subject",
        help="タイトルで返信先メッセージを検索（ChannelMessage.Read.All を追加要求）",
    )

    args = parser.parse_args()

    # --reply-to-subject を使う場合のみ ChannelMessage.Read.All を追加
    scopes = BASE_SCOPES[:]
    if args.reply_to_subject:
        scopes.append(READ_SCOPE)

    token = get_token(scopes)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # チーム / チャンネル解決
    team_id, team_display = _resolve_team(headers, args.team_name, args.team_id)
    channel_id, channel_display, membership_type = _resolve_channel(
        headers, team_id, args.channel_name, args.channel_id
    )

    # reply-to-subject で返信先メッセージを検索
    reply_to_id = args.reply_to_message_id
    if args.reply_to_subject:
        reply_to_id = _find_message_by_subject(headers, team_id, channel_id, args.reply_to_subject)

    # メンション構築
    mentions: list[dict] = []
    mention_id = 0
    mention_prefix = ""

    if args.mention_team:
        mentions.append({
            "id": mention_id,
            "mentionText": team_display,
            "mentioned": {"team": {"id": team_id, "displayName": team_display}},
        })
        mention_prefix += f'<at id="{mention_id}">{html.escape(team_display)}</at> '
        mention_id += 1

    if args.mention_channel:
        mentions.append({
            "id": mention_id,
            "mentionText": channel_display,
            "mentioned": {
                "channel": {
                    "id": channel_id,
                    "displayName": channel_display,
                    "membershipType": membership_type,
                }
            },
        })
        mention_prefix += f'<at id="{mention_id}">{html.escape(channel_display)}</at> '
        mention_id += 1

    # 本文組み立て
    if mentions:
        content_type = "html"
        if args.content_type == "text":
            content = mention_prefix + html.escape(args.message)
        else:
            content = mention_prefix + args.message
    else:
        content_type = args.content_type
        content = args.message

    # ペイロード
    payload: dict = {"body": {"contentType": content_type, "content": content}}
    if args.subject and not reply_to_id:
        payload["subject"] = args.subject
    if mentions:
        payload["mentions"] = mentions

    # 投稿
    if reply_to_id:
        url = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages/{reply_to_id}/replies"
    else:
        url = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages"

    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    result = resp.json()
    print(f"投稿完了: {result.get('webUrl', '(URL 取得不可)')}")


if __name__ == "__main__":
    main()
