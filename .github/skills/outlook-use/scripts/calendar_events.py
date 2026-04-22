#!/usr/bin/env python3
"""Manage Outlook calendar events via Microsoft Graph API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Calendars.ReadWrite", "offline_access"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_token(SCOPES)}",
        "Content-Type": "application/json",
    }


# ─── list ────────────────────────────────────────────────────────────────────

def list_events(args: argparse.Namespace) -> None:
    url = f"{GRAPH_BASE}/me/events"
    params: dict = {
        "$top": args.top,
        "$orderby": "start/dateTime asc",
        "$select": "id,subject,start,end,location,attendees,bodyPreview,isAllDay,isCancelled",
    }

    filters = []
    if args.start:
        filters.append(f"start/dateTime ge '{args.start}T00:00:00'")
    if args.end:
        filters.append(f"end/dateTime le '{args.end}T23:59:59'")
    if filters:
        params["$filter"] = " and ".join(filters)

    resp = requests.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    events = resp.json().get("value", [])

    if args.json_output:
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return

    if not events:
        print("予定が見つかりませんでした。")
        return

    for ev in events:
        _print_event(ev)


def _print_event(ev: dict) -> None:
    is_all_day = ev.get("isAllDay", False)
    start_raw = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
    end_raw = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
    start = start_raw[:10] if is_all_day else start_raw[:16].replace("T", " ")
    end = end_raw[:10] if is_all_day else end_raw[:16].replace("T", " ")
    loc = ev.get("location", {}).get("displayName", "")
    cancelled = " [キャンセル]" if ev.get("isCancelled") else ""
    all_day_tag = " [終日]" if is_all_day else ""

    print(f"{'─' * 60}")
    print(f"  ID     : {ev.get('id', '')}")
    print(f"  件名   : {ev.get('subject', '(件名なし)')}{cancelled}{all_day_tag}")
    print(f"  開始   : {start}")
    print(f"  終了   : {end}")
    if loc:
        print(f"  場所   : {loc}")
    attendees = [
        a.get("emailAddress", {}).get("address", "")
        for a in ev.get("attendees", [])
        if a.get("emailAddress", {}).get("address")
    ]
    if attendees:
        print(f"  参加者 : {', '.join(attendees)}")
    preview = ev.get("bodyPreview", "").strip()[:120]
    if preview:
        print(f"  概要   : {preview}")
    print()


# ─── create ──────────────────────────────────────────────────────────────────

def create_event(args: argparse.Namespace) -> None:
    if args.all_day:
        body = {
            "subject": args.subject,
            "isAllDay": True,
            "start": {"date": args.start.split("T")[0]},
            "end": {"date": args.end.split("T")[0]},
        }
    else:
        body = {
            "subject": args.subject,
            "start": {"dateTime": args.start, "timeZone": args.timezone},
            "end": {"dateTime": args.end, "timeZone": args.timezone},
        }

    if args.location:
        body["location"] = {"displayName": args.location}
    if args.body:
        body["body"] = {
            "contentType": "HTML" if args.html else "Text",
            "content": args.body,
        }
    if args.attendees:
        body["attendees"] = [
            {"emailAddress": {"address": addr.strip()}, "type": "required"}
            for addr in args.attendees.split(",")
            if addr.strip()
        ]

    url = f"{GRAPH_BASE}/me/events"
    resp = requests.post(url, headers=_headers(), json=body)
    resp.raise_for_status()
    ev = resp.json()

    print("予定を作成しました。")
    _print_event(ev)


# ─── delete ──────────────────────────────────────────────────────────────────

def delete_event(args: argparse.Namespace) -> None:
    url = f"{GRAPH_BASE}/me/events/{args.event_id}"
    resp = requests.delete(url, headers=_headers())
    resp.raise_for_status()
    print(f"予定を削除しました: {args.event_id}")


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook カレンダーを管理する")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    sp_list = subparsers.add_parser("list", help="予定一覧を取得")
    sp_list.add_argument("--top", type=int, default=20, help="取得件数（デフォルト 20）")
    sp_list.add_argument("--start", help="開始日フィルタ (YYYY-MM-DD)")
    sp_list.add_argument("--end", help="終了日フィルタ (YYYY-MM-DD)")
    sp_list.add_argument("--json", dest="json_output", action="store_true", help="JSON 形式で出力")

    # create
    sp_create = subparsers.add_parser("create", help="予定を作成")
    sp_create.add_argument("--subject", required=True, help="件名")
    sp_create.add_argument("--start", required=True, help="開始日時 (YYYY-MM-DDTHH:MM:SS) または日付 (YYYY-MM-DD、終日の場合)")
    sp_create.add_argument("--end", required=True, help="終了日時 (YYYY-MM-DDTHH:MM:SS) または日付 (YYYY-MM-DD、終日の場合)")
    sp_create.add_argument("--location", help="場所")
    sp_create.add_argument("--body", help="本文・説明")
    sp_create.add_argument("--html", action="store_true", help="本文を HTML 形式として送信")
    sp_create.add_argument("--attendees", help="参加者のメールアドレス（カンマ区切り）")
    sp_create.add_argument("--timezone", default="Asia/Tokyo", help="タイムゾーン（デフォルト: Asia/Tokyo）")
    sp_create.add_argument("--all-day", action="store_true", help="終日予定として作成")

    # delete
    sp_delete = subparsers.add_parser("delete", help="予定を削除")
    sp_delete.add_argument("--event-id", required=True, help="削除する予定の ID（list コマンドで確認）")

    args = parser.parse_args()

    if args.command == "list":
        list_events(args)
    elif args.command == "create":
        create_event(args)
    elif args.command == "delete":
        delete_event(args)


if __name__ == "__main__":
    main()
