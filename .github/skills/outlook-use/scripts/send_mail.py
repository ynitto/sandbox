#!/usr/bin/env python3
"""Send emails via Outlook using Microsoft Graph API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from auth import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Send", "offline_access"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_token(SCOPES)}",
        "Content-Type": "application/json",
    }


def _addr_list(value: str) -> list[dict]:
    return [
        {"emailAddress": {"address": addr.strip()}}
        for addr in value.split(",")
        if addr.strip()
    ]


def send_mail(args: argparse.Namespace) -> None:
    message: dict = {
        "subject": args.subject,
        "body": {
            "contentType": "HTML" if args.html else "Text",
            "content": args.body,
        },
        "toRecipients": _addr_list(args.to),
    }

    if args.cc:
        message["ccRecipients"] = _addr_list(args.cc)
    if args.bcc:
        message["bccRecipients"] = _addr_list(args.bcc)
    if args.reply_to:
        message["replyTo"] = _addr_list(args.reply_to)

    payload = {
        "message": message,
        "saveToSentItems": not args.no_save,
    }

    url = f"{GRAPH_BASE}/me/sendMail"
    resp = requests.post(url, headers=_headers(), json=payload)
    resp.raise_for_status()

    to_str = args.to
    print(f"メールを送信しました。")
    print(f"  宛先 : {to_str}")
    print(f"  件名 : {args.subject}")
    if args.cc:
        print(f"  CC   : {args.cc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook からメールを送信する")
    parser.add_argument("--to", required=True, help="宛先メールアドレス（カンマ区切りで複数指定可）")
    parser.add_argument("--subject", required=True, help="件名")
    parser.add_argument("--body", required=True, help="本文")
    parser.add_argument("--cc", help="CC アドレス（カンマ区切り）")
    parser.add_argument("--bcc", help="BCC アドレス（カンマ区切り）")
    parser.add_argument("--reply-to", help="Reply-To アドレス")
    parser.add_argument("--html", action="store_true", help="本文を HTML 形式として送信")
    parser.add_argument(
        "--no-save", action="store_true",
        help="送信済みフォルダに保存しない",
    )
    args = parser.parse_args()

    send_mail(args)


if __name__ == "__main__":
    main()
