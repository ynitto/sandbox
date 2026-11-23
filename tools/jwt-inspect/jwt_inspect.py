#!/usr/bin/env python3
"""JWT の中身を表示する。既定では署名を検証しない。"""
import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIME_CLAIMS = ("exp", "nbf", "iat", "auth_time")


def decode_segment(segment):
    if re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", segment) is None:
        raise ValueError("base64url に使用できない文字があります")
    unpadded = segment.rstrip("=")
    padding_size = -len(unpadded) % 4
    if len(segment) - len(unpadded) > padding_size:
        raise ValueError("base64url のパディングが不正です")
    padded = unpadded + "=" * padding_size
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except binascii.Error as error:
        raise ValueError("base64url の形式が不正です") from error


def decode_object(segment, *, allow_nonfinite_times=False):
    value = json.loads(decode_segment(segment).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON がオブジェクトではありません")
    display_value = value.copy() if allow_nonfinite_times else value
    if allow_nonfinite_times:
        for claim in TIME_CLAIMS:
            timestamp = value.get(claim)
            if type(timestamp) is float and not math.isfinite(timestamp):
                display_value[claim] = str(timestamp)
    formatted = json.dumps(display_value, indent=2, ensure_ascii=False, allow_nan=False)
    formatted.encode("utf-8")
    return value, formatted


def show_times(payload, zone, now):
    for claim in TIME_CLAIMS:
        if claim not in payload:
            continue
        value = payload[claim]
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                pass
        if type(value) not in (int, float):
            print(f"{claim}: 数値の UNIX 時刻ではありません")
            continue
        if type(value) is float and not math.isfinite(value):
            print(f"{claim}: 有限の UNIX 時刻ではありません ({value})")
            continue
        try:
            date = datetime.fromtimestamp(value, tz=timezone.utc).astimezone(zone)
            print(f"{claim}: {date.isoformat()} ({zone.key})")
        except (OverflowError, OSError, ValueError):
            print(f"{claim}: 表示可能な日時の範囲外です")
        if claim == "exp" and value <= now:
            print("警告: 期限切れ (exp)")
        if claim == "nbf" and value > now:
            print("警告: まだ有効になっていない (nbf)")


def main(argv=None, *, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("token", nargs="?", help="JWT 文字列（最優先）")
    parser.add_argument("--file", type=Path, help="JWT を読むファイル（省略時は標準入力）")
    parser.add_argument("--tz", default="UTC", help="時刻表示の IANA タイムゾーン（既定値: UTC）")
    parser.add_argument("--secret", help="HS256 署名検証用の秘密文字列")
    args = parser.parse_args(argv)
    if args.secret == "":
        print("--secret に空文字列は指定できません", file=sys.stderr)
        return 1

    try:
        zone = ZoneInfo(args.tz)
        if args.token is not None:
            token = args.token
        elif args.file is not None:
            token = args.file.read_text(encoding="utf-8")
        else:
            token = sys.stdin.read()
    except (OSError, ValueError, ZoneInfoNotFoundError) as error:
        print(f"入力またはタイムゾーンのエラー: {error}", file=sys.stderr)
        return 1

    parts = "".join(token.split()).split(".")
    if len(parts) != 3:
        print("不正な JWT: パート数は 3 である必要があります", file=sys.stderr)
        return 2
    try:
        part_name = "Header"
        header, header_json = decode_object(parts[0])
        part_name = "Payload"
        payload, payload_json = decode_object(parts[1], allow_nonfinite_times=True)
        part_name = "署名"
        signature = decode_segment(parts[2])
    except (ValueError, RecursionError) as error:
        print(f"不正な JWT ({part_name}): {error}", file=sys.stderr)
        return 2

    print(f"Header:\n{header_json}")
    print(f"Payload:\n{payload_json}")
    show_times(payload, zone, now if now is not None else datetime.now(timezone.utc).timestamp())
    print(f"署名: {'あり' if signature else 'なし'} ({len(signature)} バイト)")

    if args.secret is None:
        return 0
    if header.get("alg") != "HS256":
        print("署名検証: HS256 以外は検証できません")
        return 3

    signing_input = ".".join(parts[:2]).encode("ascii")
    expected = hmac.new(args.secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if hmac.compare_digest(signature, expected):
        print("署名検証: OK")
        return 0
    print("署名検証: NG")
    return 3


if __name__ == "__main__":
    sys.exit(main())
