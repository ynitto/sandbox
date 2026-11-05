"""cron の書式（5 項目）の解釈。取得元の schedule を、起動されたときに「いま取得する番か」の判断に使う。

    分 時 日 月 曜日      例: "0 * * * *"（毎時 0 分）・"*/10 9-18 * * 1-5"（平日 9〜18 時の 10 分ごと）

各項目は `*`・数・`a-b`・`*/n`・`a-b/n`・`a/n`・カンマ区切りの組み合わせ。月は JAN〜DEC、曜日は SUN〜SAT
（0 と 7 が日曜）も使える。`@hourly`・`@daily`（`@midnight`）・`@weekly`・`@monthly`・`@yearly`（`@annually`）も使える。
日と曜日の両方を絞ったときは、どちらかに当たれば当たり（一般的な cron と同じ）。時刻はこの PC の時刻（ローカル時刻）で見る。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .util import ConfigError

_MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
           "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
_MONTHS = {n: i + 1 for i, n in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
_DAYS = {n: i for i, n in enumerate("SUN MON TUE WED THU FRI SAT".split())}


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    weekdays: frozenset         # 0 = 日曜
    day_any: bool
    weekday_any: bool

    def _day_ok(self, t: datetime) -> bool:
        dom = t.day in self.days
        dow = (t.isoweekday() % 7) in self.weekdays
        if self.day_any and self.weekday_any:
            return True
        if self.day_any:
            return dow
        if self.weekday_any:
            return dom
        return dom or dow

    def matches(self, t: datetime) -> bool:
        return (t.month in self.months and self._day_ok(t) and t.hour in self.hours and t.minute in self.minutes)

    def previous(self, t: datetime) -> datetime | None:
        """t 以前（t を含む）で最後に当たる時刻（分の頭）。8 年さかのぼっても無ければ None。"""
        t = t.replace(second=0, microsecond=0)
        floor = t.year - 8
        one = timedelta(minutes=1)
        while t.year >= floor:
            if t.month not in self.months:
                t = t.replace(day=1, hour=0, minute=0) - one
            elif not self._day_ok(t):
                t = t.replace(hour=0, minute=0) - one
            elif t.hour not in self.hours:
                t = t.replace(minute=0) - one
            elif t.minute not in self.minutes:
                t -= one
            else:
                return t
        return None

    def next(self, t: datetime) -> datetime | None:
        """t より後で最初に当たる時刻。8 年先まで無ければ None。"""
        t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
        ceil = t.year + 8
        while t.year <= ceil:
            if t.month not in self.months:
                t = (t.replace(day=28, hour=0, minute=0) + timedelta(days=4)).replace(day=1)
            elif not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        return None


def _field(text: str, lo: int, hi: int, names: dict[str, int], what: str) -> tuple[frozenset, bool]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise ConfigError(f"{what} の間隔（/ の後ろ）は 1 以上の数です: {text}")
            step = int(s)

        def num(x: str) -> int:
            x = x.upper()
            if x in names:
                return names[x]
            if not x.isdigit():
                raise ConfigError(f"{what} に数でないものがあります: {text}")
            return int(x)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = num(x), num(y)
        else:
            a = num(part)
            b = hi if step > 1 else a
        top = 7 if what == "曜日" else hi
        if not (lo <= a <= top and lo <= b <= top and a <= b):
            raise ConfigError(f"{what} は {lo}〜{hi} の範囲で書いてください: {text}")
        out.update(range(a, b + 1, step))
    if what == "曜日" and 7 in out:
        out.discard(7)
        out.add(0)
    return frozenset(out), text == "*"


def parse(text: str) -> Cron:
    raw = str(text).strip()
    expr = _MACROS.get(raw.lower(), raw)
    parts = expr.split()
    if len(parts) != 5:
        raise ConfigError(f"schedule は cron の 5 項目（分 時 日 月 曜日）で書いてください: {text}")
    minutes, _ = _field(parts[0], 0, 59, {}, "分")
    hours, _ = _field(parts[1], 0, 23, {}, "時")
    days, day_any = _field(parts[2], 1, 31, {}, "日")
    months, _ = _field(parts[3], 1, 12, _MONTHS, "月")
    weekdays, wd_any = _field(parts[4], 0, 6, _DAYS, "曜日")
    c = Cron(raw, minutes, hours, days, months, weekdays, day_any, wd_any)
    if c.previous(datetime.now()) is None and c.next(datetime.now()) is None:
        raise ConfigError(f"schedule が当たる日がありません: {text}")
    return c
