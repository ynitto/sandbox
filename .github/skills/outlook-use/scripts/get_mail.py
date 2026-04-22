#!/usr/bin/env python3
"""Read and list emails from Outlook via Microsoft Graph API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "offline_access"]

FOLDER_ALIASES = {
    "inbox": "inbox",
    "受信トレイ": "inbox",
    "sentitems": "sentitems",
    "送信済み": "sentitems",
    "drafts": "drafts",
    "下書き": "drafts",
    "deleteditems": "deleteditems",
    "削除済み": "deleteditems",
    "junkemail": "junkemail",
    "迷惑メール": "junkemail",
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token(SCOPES)}"}


def get_message(message_id: str) -> None:
    url = f"{GRAPH_BASE}/me/messages/{message_id}"
    params = {"$select": "id,subject,from,toRecipients,receivedDateTime,isRead,body"}
    resp = requests.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    msg = resp.json()
    _print_message(msg, show_body=True)


def list_messages(args: argparse.Namespace) -> None:
    folder = FOLDER_ALIASES.get(args.folder, args.folder)
    url = f"{GRAPH_BASE}/me/mailFolders/{folder}/messages"

    params: dict = {
        "$top": args.top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview,body",
    }

    filters = []
    if args.unread_only:
        filters.append("isRead eq false")
    if filters:
        params["$filter"] = " and ".join(filters)

    if args.search:
        # $search and $filter cannot coexist; drop filter
        params.pop("$filter", None)
        params["$search"] = f'"{args.search}"'

    resp = requests.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    messages = resp.json().get("value", [])

    if args.json_output:
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        return

    if not messages:
        print("メールが見つかりませんでした。")
        return

    for msg in messages:
        _print_message(msg, show_body=args.show_body)


def _print_message(msg: dict, show_body: bool = False) -> None:
    received = msg.get("receivedDateTime", "")[:19].replace("T", " ")
    status = "未読" if not msg.get("isRead") else "既読"
    sender = msg.get("from", {}).get("emailAddress", {})
    sender_str = f"{sender.get('name', '')} <{sender.get('address', '')}>"
    print(f"{'─' * 60}")
    print(f"  [{status}] {received}")
    print(f"  ID     : {msg.get('id', '')}")
    print(f"  差出人 : {sender_str}")
    print(f"  件名   : {msg.get('subject', '(件名なし)')}")
    if show_body:
        body_content = (
            msg.get("body", {}).get("content", "")
            or msg.get("bodyPreview", "")
        )
        body_type = msg.get("body", {}).get("contentType", "text")
        if body_type == "html":
            # strip basic HTML tags for terminal display
            import re
            body_content = re.sub(r"<[^>]+>", "", body_content)
            body_content = body_content.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        preview = body_content.strip()[:1000]
        print(f"  本文   :\n{preview}")
    else:
        preview = msg.get("bodyPreview", "").strip()[:120]
        if preview:
            print(f"  プレビュー: {preview}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook メールを読み取る")
    parser.add_argument(
        "--folder", default="inbox",
        help="メールフォルダ (inbox/sentitems/drafts/deleteditems/junkemail)",
    )
    parser.add_argument("--top", type=int, default=20, help="取得件数（デフォルト 20）")
    parser.add_argument("--search", help="全文検索キーワード")
    parser.add_argument("--unread-only", action="store_true", help="未読メールのみ表示")
    parser.add_argument("--show-body", action="store_true", help="本文を表示")
    parser.add_argument("--message-id", help="特定メールの ID を指定して本文を表示")
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON 形式で出力")
    args = parser.parse_args()

    if args.message_id:
        get_message(args.message_id)
    else:
        list_messages(args)


if __name__ == "__main__":
    main()
