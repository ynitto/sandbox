#!/usr/bin/env python3
"""Convert Unix timestamps and ISO 8601 dates."""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
AUTO_MILLISECONDS = 10**12


class ArgumentParser(argparse.ArgumentParser):
    # argparse's default number pattern excludes negative decimals ending in a dot.
    def _parse_optional(self, arg_string):
        if arg_string.startswith("-") and NUMBER.fullmatch(arg_string):
            return None
        return super()._parse_optional(arg_string)


def rfc3339(value):
    # Some historical IANA offsets include seconds, which RFC 3339 cannot encode.
    offset = value.utcoffset()
    if offset is not None and offset.total_seconds() % 60:
        minute_offset = timedelta(minutes=round(offset.total_seconds() / 60))
        value = value.astimezone(timezone(minute_offset))
    return value.isoformat()


def convert(value, unit, zone):
    if NUMBER.fullmatch(value):
        number = Decimal(value)
        milliseconds = unit == "ms" or (
            unit == "auto" and number.copy_abs() >= AUTO_MILLISECONDS
        )
        numerator, denominator = number.as_integer_ratio()
        microseconds = round_ratio(numerator * (1000 if milliseconds else 1000000), denominator)
        return EPOCH + timedelta(microseconds=microseconds), True

    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    date = datetime.fromisoformat(value)
    if date.tzinfo is None and zone is not None:
        date = date.replace(tzinfo=zone)
    return date.astimezone(UTC), False


def round_ratio(numerator, denominator):
    quotient, remainder = divmod(numerator, denominator)
    twice_remainder = remainder * 2
    if twice_remainder > denominator or (twice_remainder == denominator and quotient % 2):
        return quotient + 1
    return quotient


def describe(source, instant, zone, zone_name):
    delta = instant - EPOCH
    microseconds = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return {
        "input": source,
        "epoch_s": epoch_number(microseconds, 1000000),
        "epoch_ms": epoch_number(microseconds, 1000),
        "utc": rfc3339(instant),
        "local": rfc3339(instant.astimezone(zone)),
        "timezone": zone_name,
    }


def epoch_number(microseconds, scale):
    whole, fraction = divmod(microseconds, scale)
    return microseconds / scale if fraction else whole


def print_text(result, epoch_input):
    iso_lines = [
        f"{result['timezone']}: {result['local']}",
        f"UTC: {result['utc']}",
    ]
    epoch_lines = [
        f"Epoch (s): {result['epoch_s']}",
        f"Epoch (ms): {result['epoch_ms']}",
    ]
    lines = iso_lines + epoch_lines if epoch_input else epoch_lines + iso_lines[::-1]
    print("\n".join(lines))


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("values", nargs="*", help="Unix epoch or ISO 8601 values")
    parser.add_argument("--unit", choices=("s", "ms", "auto"), default="auto")
    parser.add_argument("--tz", help="IANA time zone name")
    parser.add_argument("--now", action="store_true", help="include the current time first")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_intermixed_args(argv)

    zone = None
    if args.tz is not None:
        try:
            zone = ZoneInfo(args.tz)
        except (ZoneInfoNotFoundError, ValueError):
            parser.error(f"invalid IANA time zone: {args.tz!r}")
    zone_name = args.tz if args.tz is not None else "Local"

    stdin_mode = not args.values and not sys.stdin.isatty()
    values = (line.strip() for line in sys.stdin) if stdin_mode else iter(args.values)
    results = []
    count = 0
    printed = False
    failed = False

    def emit(result, epoch_input):
        nonlocal printed
        if args.json:
            results.append(result)
        else:
            if printed:
                print()
            print_text(result, epoch_input)
            printed = True

    if args.now:
        count += 1
        emit(describe("now", datetime.now(UTC), zone, zone_name), True)

    for value in values:
        if stdin_mode and not value:
            continue
        count += 1
        try:
            instant, epoch_input = convert(value.strip(), args.unit, zone)
            result = describe(value, instant, zone, zone_name)
        except (ValueError, OverflowError, OSError) as exc:
            print(f"{value!r}: {exc}", file=sys.stderr)
            failed = True
            continue
        emit(result, epoch_input)

    if not count:
        parser.error("provide a value, nonempty standard input, or --now")
    if args.json:
        print(json.dumps(results[0] if count == 1 and results else results))
    return (1 if stdin_mode else 2) if failed else 0


if __name__ == "__main__":
    sys.exit(main())
