#!/usr/bin/env python3
"""Summarize a CSV file using only the Python standard library."""

import argparse
import codecs
from collections import Counter
from contextlib import suppress
import csv
from datetime import datetime, time
from itertools import chain, islice
import io
import json
import math
import re
import sys
import tempfile
import unicodedata


DELIMITERS = ",\t;|"
INTEGER = re.compile(r"[+-]?[0-9]+")
DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class ReplayInput(io.RawIOBase):
    """Record stdin as it is read, so auto-detection can retry from the start."""

    def __init__(self, source, cache):
        super().__init__()
        self.source = source
        self.cache = cache

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.cache.read(len(buffer))
        if not data:
            data = self.source.read1(len(buffer))
            self.cache.write(data)
        buffer[:len(data)] = data
        return len(data)


def classify(value):
    if value.casefold() in {"true", "false", "yes", "no"}:
        return "boolean", None
    if INTEGER.fullmatch(value):
        try:
            return "integer", int(value)
        except ValueError:
            return "string", None
    if DECIMAL.fullmatch(value):
        number = float(value)
        if math.isfinite(number):
            return "decimal", number
    if "-" in value or ":" in value:
        for parse in (datetime.fromisoformat, time.fromisoformat):
            try:
                parse(value)
                return "datetime", None
            except ValueError:
                pass
    return "string", None


class Column:
    def __init__(self):
        self.types = Counter()
        self.values = set()
        self.minimum = None
        self.maximum = None

    def add(self, value):
        value = value.strip()
        if not value:
            return
        self.values.add(value)
        kind, number = classify(value)
        self.types[kind] += 1
        if number is not None:
            self.minimum = number if self.minimum is None else min(self.minimum, number)
            self.maximum = number if self.maximum is None else max(self.maximum, number)

    def summary(self, name, row_count):
        nonempty = sum(self.types.values())
        groups = self.types.copy()
        groups["number"] = groups.pop("integer", 0) + groups.pop("decimal", 0)
        kind, count = groups.most_common(1)[0]
        if not nonempty:
            kind = "string"
        elif count * 2 <= nonempty:
            kind = "mixed"
        elif kind == "number":
            kind = "decimal" if self.types["decimal"] else "integer"

        result = {
            "name": name,
            "type": kind,
            "empty_count": row_count - nonempty,
            "unique_count": len(self.values),
        }
        if kind in {"integer", "decimal"}:
            result.update(min=self.minimum, max=self.maximum)
        return result


def choose_delimiter(first_record):
    try:
        return csv.Sniffer().sniff(first_record, delimiters=DELIMITERS).delimiter
    except csv.Error:
        # Count parsed fields so separators inside quoted cells are ignored.
        def width(delimiter):
            reader = csv.reader(io.StringIO(first_record), delimiter=delimiter)
            return len(next(reader, []))

        return max(DELIMITERS, key=width)


def checked_lines(stream):
    for line in stream:
        # Validate only lines requested by the CSV reader. TextIOWrapper can
        # decode ahead into rows outside --limit.
        invalid = re.search(r"[\udc80-\udcff]", line)
        if invalid:
            byte = bytes([ord(invalid[0]) - 0xDC00])
            raise UnicodeDecodeError(stream.encoding, byte, 0, 1, "invalid input byte")
        yield line


def summarize(stream, args, encoding):
    lines = checked_lines(stream)
    delimiter = args.delimiter
    if delimiter is None:
        first_line = None if args.no_header and args.limit == 0 else next(lines, None)
        delimiter = choose_delimiter(first_line or "")
        if first_line is not None:
            lines = chain((first_line,), lines)

    records = csv.reader(lines, delimiter=delimiter, strict=True)
    names = [] if args.no_header else next(records, [])
    expected_width = None if args.no_header else len(names)
    columns = [Column() for _ in names]
    samples = []
    row_count = 0
    uneven = 0

    for row in islice(records, args.limit):
        if expected_width is None:
            expected_width = len(row)
        uneven += len(row) != expected_width
        columns.extend(Column() for _ in range(len(row) - len(columns)))
        for column, cell in zip(columns, row):
            column.add(cell)
        if len(samples) < args.sample:
            samples.append(row)
        row_count += 1

    width = len(columns)
    names.extend(f"col{i + 1}" for i in range(len(names), width))
    for row in samples:
        row.extend([""] * (width - len(row)))

    return {
        "encoding": encoding,
        "delimiter": delimiter,
        "row_count": row_count,
        "column_count": width,
        "headers": names,
        "limit": args.limit,
        "uneven_row_count": uneven,
        "columns": [
            column.summary(name, row_count) for name, column in zip(names, columns)
        ],
        "sample_rows": samples,
    }


def inspect_csv(source, args, rewind):
    def inspect(buffer, encoding):
        stream = io.TextIOWrapper(buffer, encoding=encoding, errors="surrogateescape", newline="")
        try:
            return summarize(stream, args, encoding)
        finally:
            stream.detach()

    if args.encoding:
        return inspect(source, args.encoding)
    if source.peek(len(codecs.BOM_UTF8)).startswith(codecs.BOM_UTF8):
        return inspect(source, "utf-8-sig")
    try:
        return inspect(source, "utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return rewind(inspect, "shift_jis")
    except UnicodeDecodeError:
        pass
    try:
        return rewind(inspect, "cp932")
    except UnicodeDecodeError as error:
        raise ValueError(f"UTF-8、Shift_JIS、cp932 のいずれでもデコードできません ({error})") from error


def cell_width(value):
    return sum(
        2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        for char in value if not unicodedata.combining(char)
    )


def display_table(rows):
    visible = [
        [re.sub(r"[\x00-\x1f\x7f]", lambda match: repr(match[0])[1:-1], cell)
         for cell in row]
        for row in rows
    ]
    widths = [max(map(cell_width, cells)) for cells in zip(*visible)]
    for row in visible:
        padded = [cell + " " * (width - cell_width(cell)) for cell, width in zip(row, widths)]
        print(" | ".join(padded).rstrip())


def display(result):
    print(f"文字コード: {result['encoding']}")
    print(f"区切り文字: {result['delimiter']!r}")
    limit = result["limit"]
    note = "" if limit is None else f"（先頭 {limit} 行時点の概算、総行数は未集計）"
    print(f"行数: {result['row_count']}{note}")
    print(f"列数: {result['column_count']}")
    if result["columns"]:
        rows = [["列名", "型", "空欄数", "ユニーク数", "最小", "最大"]]
        for column in result["columns"]:
            keys = ("name", "type", "empty_count", "unique_count", "min", "max")
            rows.append([str(column.get(key, "")) for key in keys])
        display_table(rows)
    if result["sample_rows"] and result["headers"]:
        print("\nサンプル")
        display_table([result["headers"], *result["sample_rows"]])


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 以上の整数を指定してください")
    return number


def parse_delimiter(value):
    value = {"tab": "\t", r"\t": "\t"}.get(value, value)
    if len(value) != 1 or value in "\r\n":
        raise argparse.ArgumentTypeError("改行以外の 1 文字、または tab を指定してください")
    return value


def main():
    parser = argparse.ArgumentParser(description="CSV ファイルの概要を表示します。")
    parser.add_argument("file", help="CSV ファイルのパス。- は標準入力")
    parser.add_argument("--delimiter", type=parse_delimiter, help="区切り文字")
    parser.add_argument("--no-header", action="store_true", help="すべての行をデータとして扱う")
    parser.add_argument("--sample", type=nonnegative, default=5, help="表示するサンプル行数")
    parser.add_argument("--limit", type=nonnegative, help="集計する先頭データ行数")
    parser.add_argument("--json", action="store_true", help="JSON 形式で出力")
    parser.add_argument("--encoding", help="入力の文字コード")
    args = parser.parse_args()

    try:
        if args.file != "-":
            with open(args.file, "rb") as source:
                def retry(inspect, encoding):
                    source.seek(0)
                    return inspect(source, encoding)

                result = inspect_csv(source, args, retry)
        elif args.encoding:
            result = inspect_csv(sys.stdin.buffer, args, None)
        else:
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as cache:
                cache.write(sys.stdin.buffer.read(len(codecs.BOM_UTF8)))
                cache.seek(0)

                def retry(inspect, encoding):
                    cache.seek(0)
                    with io.BufferedReader(ReplayInput(sys.stdin.buffer, cache)) as source:
                        return inspect(source, encoding)

                with io.BufferedReader(ReplayInput(sys.stdin.buffer, cache)) as source:
                    result = inspect_csv(source, args, retry)
    except (OSError, LookupError, ValueError, csv.Error) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2

    try:
        if result["uneven_row_count"]:
            print(f"警告: 列数が揃っていない行: {result['uneven_row_count']} 件", file=sys.stderr)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            display(result)
        sys.stdout.flush()
    except (OSError, ValueError) as error:
        if isinstance(error, OSError):
            # Prevent another failed flush during interpreter shutdown.
            with suppress(OSError):
                sys.stdout.close()
        print(f"エラー: 出力できません ({error})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
