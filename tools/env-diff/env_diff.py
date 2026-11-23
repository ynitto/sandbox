#!/usr/bin/env python3
"""Compare two .env files. Values are hidden by default to keep secrets out of CI logs."""

import argparse
import json
from pathlib import Path
import sys


def parse_env(path):
    values = {}
    lines_by_key = {}
    with path.open(encoding="utf-8-sig") as source:
        for number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or not key or any(char.isspace() for char in key):
                raise ValueError(f"{path}: {number} 行目が KEY=VALUE 形式ではありません")
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            values[key] = value
            lines_by_key.setdefault(key, []).append(number)
    duplicates = [
        {"key": key, "lines": lines_by_key[key]}
        for key in sorted(lines_by_key)
        if len(lines_by_key[key]) > 1
    ]
    return values, duplicates


def describe_value(value, show_values):
    result = {"empty": value == ""}
    if show_values:
        result["value"] = value
    return result


def compare(a, b, duplicates_a, duplicates_b, show_values):
    return {
        "only_in_a": [
            {"key": key, **describe_value(a[key], show_values)}
            for key in sorted(a.keys() - b.keys())
        ],
        "only_in_b": [
            {"key": key, **describe_value(b[key], show_values)}
            for key in sorted(b.keys() - a.keys())
        ],
        "different": [
            {
                "key": key,
                "a": describe_value(a[key], show_values),
                "b": describe_value(b[key], show_values),
            }
            for key in sorted(a.keys() & b.keys())
            if a[key] != b[key]
        ],
        "duplicates_a": duplicates_a,
        "duplicates_b": duplicates_b,
    }


def format_value(entry):
    if entry["empty"]:
        return " (空)"
    if "value" in entry:
        return f" = {entry['value']!r}"
    return ""


def print_report(result):
    labels = {
        "only_in_a": "A にだけあるキー",
        "only_in_b": "B にだけあるキー",
        "different": "値が違うキー",
        "duplicates_a": "A の重複キー",
        "duplicates_b": "B の重複キー",
    }
    for group, entries in result.items():
        if not entries:
            continue
        print(labels[group])
        for entry in entries:
            key = entry["key"]
            if group == "different":
                details = ((side.upper(), format_value(entry[side])) for side in ("a", "b"))
                sides = [label + detail for label, detail in details if detail]
                suffix = f" [{', '.join(sides)}]" if sides else ""
            elif "lines" in entry:
                lines = ", ".join(map(str, entry["lines"]))
                suffix = f" ({lines} 行目)"
            else:
                suffix = format_value(entry)
            print(f"  {key}{suffix}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_a", type=Path, metavar="ファイルA")
    parser.add_argument("file_b", type=Path, metavar="ファイルB")
    parser.add_argument("--show-values", action="store_true", help="差分に値を表示する")
    parser.add_argument("--json", action="store_true", help="JSON 形式で出力する")
    args = parser.parse_args()
    def read_input(path):
        try:
            return parse_env(path)
        except UnicodeDecodeError:
            parser.error(f"{path}: UTF-8 として読み込めません")
        except OSError as error:
            parser.error(f"{path}: {error.strerror}")
        except ValueError as error:
            parser.error(str(error))

    a, duplicates_a = read_input(args.file_a)
    b, duplicates_b = read_input(args.file_b)
    result = compare(a, b, duplicates_a, duplicates_b, args.show_values)
    has_differences = any(result.values())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif has_differences:
        print_report(result)
    else:
        print("差分なし")
    return int(has_differences)


if __name__ == "__main__":
    sys.exit(main())
