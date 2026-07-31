from __future__ import annotations
# cron.py — 元 agent-loop.py の 534-676 行目（機械分割・内容無改変）。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
# ---------------------------------------------------------------------------
# Cron 式パーサー
# ---------------------------------------------------------------------------

class CronExpression:
    """5フィールド cron 式 (分 時 日 月 曜日) のパーサー。

    形式: "分 時 日 月 曜日"
    例:   "0 9 * * 1-5"   → 平日9:00
          "*/30 * * * *"  → 30分ごと
          "0 0 1 * *"     → 毎月1日0:00

    DOM と DOW が両方指定された場合は Vixie cron と同じ OR ロジックを使用する。
    """

    def __init__(self, expr: str) -> None:
        self._expr = expr.strip()
        fields = self._expr.split()
        if len(fields) != 5:
            raise ValueError(
                f"cron 式は「分 時 日 月 曜日」の5フィールドで指定してください: {expr!r}"
            )
        min_f, hour_f, dom_f, month_f, dow_f = fields
        self._mins = self._parse_field(min_f, 0, 59)
        self._hours = self._parse_field(hour_f, 0, 23)
        self._doms = self._parse_field(dom_f, 1, 31)
        self._months = self._parse_field(month_f, 1, 12)
        raw_dows = self._parse_field(dow_f, 0, 7)
        self._dows = {0 if v == 7 else v for v in raw_dows}  # 7 → 0 (日曜)
        self._dom_star = dom_f == "*"
        self._dow_star = dow_f == "*"

    def _parse_field(self, field: str, lo: int, hi: int) -> set[int]:
        values: set[int] = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_str = part.rsplit("/", 1)
                step = int(step_str)
                if step < 1:
                    raise ValueError(f"ステップは1以上で指定してください: {field!r}")
            if part == "*":
                values.update(range(lo, hi + 1, step))
            elif "-" in part:
                a, b = part.split("-", 1)
                values.update(range(int(a), int(b) + 1, step))
            else:
                v = int(part)
                values.update(range(v, hi + 1, step) if step > 1 else [v])
        return {v for v in values if lo <= v <= hi}

    def next_run(self, after: _dt.datetime) -> _dt.datetime:
        """after の1分後以降で最初に一致する時刻を返す（秒=0、ローカルタイム基準）。"""
        t = (after + _dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = after + _dt.timedelta(days=366 * 4)

        while t <= limit:
            if t.month not in self._months:
                t = self._next_valid_month(t)
                continue

            # DOM と DOW の評価 (Vixie cron: 両方指定時は OR)
            cron_dow = (t.weekday() + 1) % 7  # Python Mon=0..Sun=6 → cron Sun=0..Sat=6
            dom_ok = t.day in self._doms
            dow_ok = cron_dow in self._dows

            if self._dom_star and self._dow_star:
                day_ok = True
            elif self._dom_star:
                day_ok = dow_ok
            elif self._dow_star:
                day_ok = dom_ok
            else:
                day_ok = dom_ok or dow_ok

            if not day_ok:
                t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if t.hour not in self._hours:
                next_hours = [h for h in sorted(self._hours) if h > t.hour]
                if next_hours:
                    t = t.replace(hour=next_hours[0], minute=0)
                else:
                    t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if t.minute not in self._mins:
                next_mins = [m for m in sorted(self._mins) if m > t.minute]
                if next_mins:
                    t = t.replace(minute=next_mins[0])
                else:
                    t = (t + _dt.timedelta(hours=1)).replace(minute=0)
                continue

            return t

        raise ValueError(f"次回実行時刻が4年以内に見つかりません: {self._expr!r}")

    def _next_valid_month(self, t: _dt.datetime) -> _dt.datetime:
        year, month = t.year, t.month + 1
        for _ in range(25):
            if month > 12:
                month = 1
                year += 1
            if month in self._months:
                return t.replace(year=year, month=month, day=1, hour=0, minute=0)
            month += 1
        raise ValueError(f"有効な月が見つかりません: {self._expr!r}")

    def __str__(self) -> str:
        return self._expr


def configure_file_logging() -> Path:
    """~/.agent/agent-loop.log へのファイルハンドラを追加する。"""
    log_file = _AGENT_HOME / LOG_FILE_NAME
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    resolved_log_file = str(log_file.resolve())
    for handler in root_logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and getattr(handler, "baseFilename", "") == resolved_log_file:
            return log_file

    file_handler = TimedRotatingFileHandler(
        filename=resolved_log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(root_logger.level)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)
    return log_file


