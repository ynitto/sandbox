#!/usr/bin/env python3
"""Inspect and edit URL query parameters."""

import argparse
import json
import re
import sys
from urllib.parse import quote, unquote, urlencode, urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}
INVALID_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:")
AUTHORITY_SCHEMES = {*DEFAULT_PORTS, "ssh"}


def decode(value, *, form=False):
    try:
        if INVALID_ESCAPE.search(value):
            raise ValueError("invalid percent escape")
        return unquote(value.replace("+", " ") if form else value, errors="strict")
    except ValueError:
        print(f"warning: invalid percent encoding in {value!r}", file=sys.stderr)
        return value


def split_url(url, fix_scheme):
    if not url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("URL is empty or contains whitespace or control characters")
    if fix_scheme and (
        not SCHEME.match(url) or re.match(r"[^:/?#]+:[0-9]+(?:[/?#]|$)", url)
    ):
        url = "https:" + url if url.startswith("//") else "https://" + url
    parts = urlsplit(url)
    if not parts.scheme:
        raise ValueError("URL must include a scheme (or use --fix-scheme)")
    if (parts.scheme in AUTHORITY_SCHEMES or parts.netloc) and not parts.hostname:
        raise ValueError("URL must include a host")
    authority = parts.netloc.rsplit("@", 1)[-1]
    if authority.count(":") > 1 and not authority.startswith("["):
        raise ValueError("IPv6 hosts must be enclosed in brackets")
    if authority.startswith("[") and not re.fullmatch(r"\[[^\]]+\](?::[0-9]*)?", authority):
        raise ValueError("invalid bracketed host or port")
    port = parts.port
    default = DEFAULT_PORTS.get(parts.scheme) if port is None else None
    components = {
        "scheme": parts.scheme,
        "username": parts.username,
        "password": parts.password,
        "host": parts.hostname,
        "port": default if port is None else port,
        "port_is_default": default is not None,
        "path": parts.path,
        "fragment": parts.fragment,
    }
    return url, components, parse_query(parts.query)


def parse_query(query):
    params = []
    for field in query.split("&"):
        if field:
            key, separator, value = field.partition("=")
            params.append((decode(key, form=True), decode(value, form=True) if separator else None))
    return params


def assignment(argument):
    key, separator, value = argument.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError("expected key=value")
    return decode(key, form=True), value


def rebuild(url, params, args):
    drop_keys = {decode(key, form=True) for key in args.drop}
    edited = [(key, value) for key, value in params if key not in drop_keys]
    for key, value in args.set:
        index = next((i for i, (name, _) in enumerate(edited) if name == key), len(edited))
        edited = [(name, old) for name, old in edited if name != key]
        edited.insert(index, (key, value))
    edited.extend(args.add)
    query = "&".join(
        urlencode([(key, "" if value is None else value)]).removesuffix("=")
        if value is None else urlencode([(key, value)])
        for key, value in edited
    )
    prefix, fragment_marker, fragment = url.partition("#")
    return prefix.partition("?")[0] + ("?" + query if edited else "") + fragment_marker + fragment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--decode")
    modes.add_argument("--encode")
    parser.add_argument("--safe")
    parser.add_argument("--fix-scheme", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--set", type=assignment, action="append", default=[])
    parser.add_argument("--add", type=assignment, action="append", default=[])
    parser.add_argument("--drop", action="append", default=[])
    args = parser.parse_args()
    editing = bool(args.set or args.add or args.drop)

    if args.safe is not None and args.encode is None:
        parser.error("--safe requires --encode")
    if args.encode is not None or args.decode is not None:
        if args.url is not None or args.fix_scheme or args.json or editing:
            parser.error("--encode and --decode cannot be combined with URL arguments or options")
        print(quote(args.encode, safe=args.safe or "") if args.encode is not None else decode(args.decode))
        return
    if args.url is None:
        parser.error("a URL, --decode, or --encode is required")

    try:
        url, components, params = split_url(args.url, args.fix_scheme)
        rebuilt = rebuild(url, params, args) if editing else None
    except ValueError as error:
        parser.error(str(error))

    if args.json:
        result = {
            "components": components,
            "params": [{"key": key, "value": value} for key, value in params],
        }
        if rebuilt is not None:
            result["rebuilt"] = rebuilt
        print(json.dumps(result, ensure_ascii=False))
        return
    for name, value in components.items():
        if name == "port_is_default" or (name in {"username", "password", "port"} and value is None):
            continue
        suffix = " (既定)" if name == "port" and components["port_is_default"] else ""
        print(f"{name}: {'' if value is None else value}{suffix}")
    print("params:")
    for key, value in params:
        print(f"  {key!r}" + (" (値なし)" if value is None else f" = {value!r}"))
    if rebuilt is not None:
        print(f"rebuilt: {rebuilt}")


if __name__ == "__main__":
    main()
