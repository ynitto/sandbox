#!/usr/bin/env python3
"""Print upcoming runs for a five-field cron expression."""

import argparse
from datetime import date, datetime, timedelta, timezone
from itertools import product
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
WEEKDAYS = "SUN MON TUE WED THU FRI SAT".split()
FIELDS = (
    ("分", 0, 59, ()),
    ("時", 0, 23, ()),
    ("日", 1, 31, ()),
    ("月", 1, 12, MONTHS),
    ("曜日", 0, 7, WEEKDAYS),
)
UTC = timezone.utc
DAY = timedelta(days=1)
SEARCH_DAYS = 8 * 366


def parse_field(field, minimum, maximum, names):
    def parse_value(token):
        if re.fullmatch(r"[0-9]+", token):
            value = int(token)
        elif token.upper() in names:
            value = minimum + names.index(token.upper())
        else:
            raise ValueError(f"無効な値または名前 {token!r}")
        if not minimum <= value <= maximum:
            raise ValueError(f"値 {value} は {minimum}〜{maximum} の範囲外です")
        return value

    values = set()
    for item in field.split(","):
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not re.fullmatch(r"[0-9]+", step_text) or int(step_text) == 0:
                raise ValueError(f"ステップ {step_text!r} は正の整数で指定してください")
            step = int(step_text)
        if base == "*":
            low, high = minimum, maximum
        elif "-" in base:
            first, last = base.split("-", 1)
            low, high = parse_value(first), parse_value(last)
            if low > high:
                raise ValueError(f"範囲 {base!r} の始点が終点を超えています")
        elif slash:
            raise ValueError("ステップには * または範囲を指定してください")
        else:
            low = high = parse_value(base)
        values.update(range(low, high + 1, step))
    return values


class Schedule:
    def __init__(self, expression):
        fields = expression.split()
        if len(fields) != len(FIELDS):
            raise ValueError(f"cron 式は5フィールド必要です（指定は {len(fields)}）")
        parsed = []
        for field, (label, minimum, maximum, names) in zip(fields, FIELDS):
            try:
                parsed.append(parse_field(field, minimum, maximum, names))
            except ValueError as error:
                raise ValueError(f"{label}フィールド {field!r}: {error}") from error
        minutes, hours, self.days, self.months, self.weekdays = parsed
        if 7 in self.weekdays:
            self.weekdays.remove(7)
            self.weekdays.add(0)
        self.day_has_star = fields[2].startswith("*")
        self.weekday_has_star = fields[4].startswith("*")
        self.times = tuple(product(sorted(hours), sorted(minutes)))

    def matches_date(self, day):
        if day.month not in self.months:
            return False
        day_matches = day.day in self.days
        weekday_matches = (day.weekday() + 1) % 7 in self.weekdays
        if self.day_has_star or self.weekday_has_star:
            return day_matches and weekday_matches
        return day_matches or weekday_matches

    def runs_on(self, day, zone):
        if not self.matches_date(day):
            return []
        runs = []
        for hour, minute in self.times:
            wall_time = datetime(day.year, day.month, day.day, hour, minute)
            for fold in (0, 1):
                local = wall_time.replace(tzinfo=zone, fold=fold)
                try:
                    instant = local.astimezone(UTC)
                    round_trip = instant.astimezone(zone)
                except OverflowError:
                    continue
                if round_trip.fold == fold and round_trip.replace(tzinfo=None) == wall_time:
                    runs.append(instant)
        return runs

    def upcoming(self, start, zone):
        start = start.astimezone(UTC)
        utc_day = start.date()
        since_match = 0
        # UTC offset magnitudes are less than a day. Keep adjacent local
        # dates available so even transitions across midnight stay ordered.
        candidates = {}
        while since_match <= SEARCH_DAYS:
            ordinal = utc_day.toordinal()
            for nearby in range(max(1, ordinal - 1), min(date.max.toordinal(), ordinal + 1) + 1):
                if nearby not in candidates:
                    candidates[nearby] = self.runs_on(date.fromordinal(nearby), zone)
            runs = sorted(
                instant
                for nearby in candidates.values()
                for instant in nearby
                if instant.date() == utc_day and instant > start
            )
            for instant in runs:
                since_match = 0
                yield instant.astimezone(zone)
            if utc_day == date.max:
                return
            candidates.pop(ordinal - 1, None)
            utc_day += DAY
            since_match += 1


def local_zone():
    if os.environ.get("TZ"):
        try:
            return ZoneInfo(os.environ["TZ"].lstrip(":"))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        with open("/etc/localtime", "rb") as zone_file:
            return ZoneInfo.from_file(zone_file)
    except (OSError, ValueError):
        return datetime.now().astimezone().tzinfo


def main(argv=None):
    parser = argparse.ArgumentParser(description="cron 式の次回実行時刻を表示します")
    parser.add_argument("expression", metavar="cron式")
    parser.add_argument("--count", type=int, default=5, help="表示件数（既定: 5）")
    parser.add_argument("--from", dest="start", metavar="ISO日時", help="検索の起点（既定: 現在）")
    parser.add_argument("--tz", metavar="IANA名", help="タイムゾーン（既定: ローカル）")
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("--count は正の整数で指定してください")
    try:
        schedule = Schedule(args.expression)
    except ValueError as error:
        parser.error(str(error))
    try:
        zone = ZoneInfo(args.tz) if args.tz is not None else local_zone()
    except (ValueError, ZoneInfoNotFoundError):
        parser.error(f"--tz: タイムゾーン {args.tz!r} が見つかりません")
    try:
        if args.start is None:
            start = datetime.now(zone)
        else:
            start_text = args.start
            if start_text.endswith("Z"):
                start_text = start_text[:-1] + "+00:00"
            start = datetime.fromisoformat(start_text)
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        start = start.astimezone(UTC)
    except (ValueError, OverflowError):
        parser.error(f"--from: 無効な ISO 日時 {args.start!r}")

    for count, run in enumerate(schedule.upcoming(start, zone), 1):
        weekday = WEEKDAYS[(run.weekday() + 1) % 7].title()
        print(f"{run.date().isoformat()} {run.hour:02d}:{run.minute:02d} ({weekday})")
        if count == args.count:
            return 0
    parser.exit(1, "次の実行時刻が検索期間内に見つかりませんでした。\n")


if __name__ == "__main__":
    raise SystemExit(main())
