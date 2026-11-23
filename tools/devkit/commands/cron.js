"use strict";

const { ArgParser, ArgTypeError, UsageError } = require("../core/args");
const { pyRepr } = require("../core/py");
const { localZoneName } = require("../core/tz");
const {
  isIanaZone, parseIsoDateTime, isoToEpochMicro, floorSeconds, utcParts, localParts, localCandidates,
  MIN_SECOND, MAX_SECOND, DAY_SECONDS,
} = require("../core/datetime");

const MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(" ");
const WEEKDAYS = "SUN MON TUE WED THU FRI SAT".split(" ");
const FIELDS = [
  ["分", 0, 59, []], ["時", 0, 23, []], ["日", 1, 31, []],
  ["月", 1, 12, MONTHS], ["曜日", 0, 7, WEEKDAYS],
];
const SEARCH_DAYS = 8 * 366;
const help = "cron 式の次回実行時刻を表示します";
const usage = "devkit cron [--count COUNT] [--from ISO日時] [--tz IANA名] cron式";

function parseField(field, [label, min, max, names]) {
  function parseValue(token) {
    const index = names.indexOf(token.toUpperCase());
    if (!/^[0-9]+$/.test(token) && index < 0) {
      throw new UsageError(`無効な値または名前 ${pyRepr(token)}`);
    }
    const value = index < 0 ? BigInt(token) : BigInt(min + index);
    if (value < min || value > max) {
      throw new UsageError(`値 ${value} は ${min}〜${max} の範囲外です`);
    }
    return Number(value);
  }

  const values = new Set();
  try {
    for (const item of field.split(",")) {
      const slash = item.indexOf("/");
      const base = slash === -1 ? item : item.slice(0, slash);
      let step = 1;
      if (slash !== -1) {
        const raw = item.slice(slash + 1);
        if (!/^[0-9]+$/.test(raw) || Number(raw) === 0) {
          throw new UsageError(`ステップ ${pyRepr(raw)} は正の整数で指定してください`);
        }
        step = Number(raw);
      }
      let low, high;
      const dash = base.indexOf("-");
      if (base === "*") {
        [low, high] = [min, max];
      } else if (dash !== -1) {
        low = parseValue(base.slice(0, dash));
        high = parseValue(base.slice(dash + 1));
        if (low > high) {
          throw new UsageError(`範囲 ${pyRepr(base)} の始点が終点を超えています`);
        }
      } else {
        if (slash !== -1) throw new UsageError("ステップには * または範囲を指定してください");
        low = high = parseValue(base);
      }
      for (let value = low; value <= high; value += step) values.add(value);
    }
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    throw new UsageError(`${label}フィールド ${pyRepr(field)}: ${error.message}`);
  }
  return values;
}

function parseSchedule(expression) {
  const fields = expression.trim().split(/\s+/u).filter(Boolean);
  if (fields.length !== FIELDS.length) {
    throw new UsageError(`cron 式は5フィールド必要です（指定は ${fields.length}）`);
  }
  const [minutes, hours, days, months, weekdays] = fields.map((field, i) => parseField(field, FIELDS[i]));
  if (weekdays.delete(7)) weekdays.add(0);
  const matchBoth = fields[2].startsWith("*") || fields[4].startsWith("*");

  return (dayStart, zone) => {
    const date = utcParts(dayStart);
    const weekday = new Date(dayStart * 1000).getUTCDay();
    const dayMatch = days.has(date.day);
    const weekdayMatch = weekdays.has(weekday);
    if (!months.has(date.month) || !(matchBoth ? dayMatch && weekdayMatch : dayMatch || weekdayMatch)) {
      return [];
    }

    const runs = [];
    for (const hour of hours) {
      for (const minute of minutes) {
        runs.push(...localCandidates(zone, { ...date, hour, minute }));
      }
    }
    return runs;
  };
}

function* upcoming(start, zone, runsOn) {
  const startSecond = floorSeconds(start);
  const candidates = new Map();
  let sinceMatch = 0;
  for (let day = Math.floor(startSecond / DAY_SECONDS) * DAY_SECONDS;
    day <= MAX_SECOND && sinceMatch <= SEARCH_DAYS;
    day += DAY_SECONDS, sinceMatch += 1) {
    // Adjacent local dates can overlap in UTC when an offset changes at midnight.
    candidates.delete(day - 2 * DAY_SECONDS);
    for (let localDay = Math.max(MIN_SECOND, day - DAY_SECONDS);
      localDay <= Math.min(MAX_SECOND, day + DAY_SECONDS); localDay += DAY_SECONDS) {
      if (!candidates.has(localDay)) candidates.set(localDay, runsOn(localDay, zone));
    }
    const runs = [...candidates.values()].flat()
      .filter((instant) => instant > startSecond && instant >= day && instant < day + DAY_SECONDS)
      .sort((a, b) => a - b);
    for (const instant of runs) {
      sinceMatch = 0;
      yield instant;
    }
  }
}

function formatRun(zone, instant) {
  const wall = localParts(zone, instant);
  const isoDate = [String(wall.year).padStart(4, "0"), wall.month, wall.day]
    .map((part) => String(part).padStart(2, "0")).join("-");
  const time = [wall.hour, wall.minute].map((part) => String(part).padStart(2, "0")).join(":");
  const day = new Date(`${isoDate}T00:00:00Z`).getUTCDay();
  const weekday = WEEKDAYS[day];
  return `${isoDate} ${time} (${weekday[0]}${weekday.slice(1).toLowerCase()})\n`;
}

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit cron", usage, description: help, negativePattern: /^-\d+$/ });
  parser.addPositional("expression", { metavar: "cron式" });
  parser.addOption("--count", {
    key: "count", def: 5n, help: "表示件数（既定: 5）",
    type(raw) {
      const normalized = raw.trim();
      if (!/^[+-]?[0-9](?:_?[0-9])*$/.test(normalized)) {
        throw new ArgTypeError(`invalid int value: ${pyRepr(raw)}`);
      }
      return BigInt(normalized.replaceAll("_", ""));
    },
  });
  parser.addOption("--from", { key: "start", metavar: "ISO日時", help: "検索の起点（既定: 現在）" });
  parser.addOption("--tz", { key: "zone", metavar: "IANA名", help: "タイムゾーン（既定: ローカル）" });
  const args = parser.parse(argv);
  if (args.count < 1n) throw new UsageError("--count は正の整数で指定してください");
  const runsOn = parseSchedule(args.expression);
  const envZone = io.env.TZ?.replace(/^:+/, "");
  const zone = args.zone ?? (isIanaZone(envZone) ? envZone : localZoneName());
  if (!isIanaZone(zone)) {
    throw new UsageError(`--tz: タイムゾーン ${pyRepr(zone)} が見つかりません`);
  }

  let start = BigInt(Date.now()) * 1000n;
  if (args.start !== undefined) {
    try {
      start = isoToEpochMicro(parseIsoDateTime(args.start), zone);
      const seconds = floorSeconds(start);
      if (seconds < MIN_SECOND || seconds > MAX_SECOND) throw new RangeError();
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      throw new UsageError(`--from: 無効な ISO 日時 ${pyRepr(args.start)}`);
    }
  }
  let remaining = args.count;
  for (const instant of upcoming(start, zone, runsOn)) {
    io.out(formatRun(zone, instant));
    remaining -= 1n;
    if (remaining === 0n) return 0;
  }
  io.err("次の実行時刻が検索期間内に見つかりませんでした。\n");
  return 1;
}

module.exports = { name: "cron", help, usage, run };
