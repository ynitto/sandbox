#!/usr/bin/env python3
"""IPv4 と IPv6 のサブネットを計算する CLI。"""

import argparse
import ipaddress
import json


LABELS = {
    "version": "IP バージョン",
    "network_address": "ネットワークアドレス",
    "broadcast_address": "ブロードキャストアドレス",
    "netmask": "ネットマスク",
    "prefix_length": "プレフィックス長",
    "total_addresses": "アドレス総数",
    "first_usable_host": "最初の使用可能ホスト",
    "last_usable_host": "最後の使用可能ホスト",
    "usable_host_count": "使用可能ホスト数",
    "wildcard_mask": "ワイルドカードマスク",
    "is_private": "プライベート",
    "is_loopback": "ループバック",
    "is_link_local": "リンクローカル",
    "is_multicast": "マルチキャスト",
    "is_global": "グローバル",
}


def parse_network(value):
    _, separator, prefix = value.partition("/")
    if not separator or not prefix.isascii() or not prefix.isdecimal():
        raise argparse.ArgumentTypeError(
            f"無効な CIDR: {value!r}（IP アドレス/プレフィックス長を指定してください）"
        )
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"無効な CIDR: {value!r}") from error


def parse_address(value):
    try:
        return ipaddress.ip_address(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"無効な IP アドレス: {value!r}") from error


def network_info(network):
    ipv4 = network.version == 4
    first = network.network_address
    last = network[-1]
    excluded_per_end = int(ipv4 and network.prefixlen < 31)

    return {
        "version": network.version,
        "network_address": str(first),
        "broadcast_address": str(last) if ipv4 else None,
        "netmask": str(network.netmask) if ipv4 else None,
        "prefix_length": network.prefixlen,
        "total_addresses": network.num_addresses,
        "first_usable_host": str(first + excluded_per_end),
        "last_usable_host": str(last - excluded_per_end),
        "usable_host_count": network.num_addresses - 2 * excluded_per_end,
        "wildcard_mask": str(network.hostmask) if ipv4 else None,
        "is_private": network.is_private,
        "is_loopback": network.is_loopback,
        "is_link_local": network.is_link_local,
        "is_multicast": network.is_multicast,
        "is_global": network.is_global,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="ネットワークの情報を表示")
    info.add_argument("cidr", metavar="CIDR", type=parse_network)
    info.add_argument("--json", action="store_true", help="JSON 形式で出力")

    contains = commands.add_parser("contains", help="IP アドレスが範囲内か判定")
    contains.add_argument("cidr", metavar="CIDR", type=parse_network)
    contains.add_argument("ip", metavar="IP", type=parse_address)

    overlap = commands.add_parser("overlap", help="ネットワークの重なりを判定")
    overlap.add_argument("cidr", metavar="CIDR1", type=parse_network)
    overlap.add_argument("other", metavar="CIDR2", type=parse_network)

    args = parser.parse_args(argv)
    if args.command == "info":
        result = network_info(args.cidr)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for key, label in LABELS.items():
                value = result[key]
                if value is None and key != "broadcast_address":
                    continue
                print(f"{label}: {'なし' if value is None else value}")
        return 0

    other = args.ip if args.command == "contains" else args.other
    if args.cidr.version != other.version:
        parser.error("IPv4 と IPv6 は比較できません（IP バージョンが異なります）")

    if args.command == "contains":
        included = other in args.cidr
        print("含まれる" if included else "含まれない")
        return 0 if included else 1

    if not args.cidr.overlaps(other):
        print("重ならない")
        return 1
    intersection = max((args.cidr, other), key=lambda network: network.prefixlen)
    print(f"重なる: {intersection} ({intersection.network_address} ～ {intersection[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
