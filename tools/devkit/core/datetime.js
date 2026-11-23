"use strict";

const { pyRepr } = require("./py");
const { isValidZone, partsAt, offsetSecondsAt, wallCandidates, wallToInstantFold0 } = require("./tz");

const MICROS_PER_SECOND = 1000000n;
const MIN_SECOND = -62135596800;
const MAX_SECOND = 253402300799;
const DAY_SECONDS = 86400;
const DATE = /^(\d{4})(?:(-?)(\d{2})\2(\d{2})|(-?)W(\d{2})(?:\5([1-7]))?)/u;
const CLOCK = /^(\d{2})(?:(:?)(\d{2})(?:\2(\d{2}))?)?(?:[.,](\d+))?$/u;

function wallSeconds(wall) {
  const date = new Date(0);
  date.setUTCFullYear(wall.year, wall.month - 1, wall.day);
  date.setUTCHours(wall.hour, wall.minute, wall.second, 0);
  return date.getTime() / 1000;
}

function utcParts(seconds) {
  const date = new Date(seconds * 1000);
  return {
    year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate(),
    hour: date.getUTCHours(), minute: date.getUTCMinutes(), second: date.getUTCSeconds(),
  };
}

function parseClock(text, invalid) {
  const match = CLOCK.exec(text);
  if (!match || match[0] !== text) throw invalid();
  const [, hour, , minute = "0", second = "0", fraction = ""] = match;
  return [Number(hour), Number(minute), Number(second), Number(fraction.slice(0, 6).padEnd(6, "0"))];
}

function parseOffset(text, invalid) {
  if (!text) return null;
  if (text === "Z" || text === "z") return 0;
  const [hour, minute, second, micro] = parseClock(text.slice(1), invalid);
  const whole = hour * 3600 + minute * 60 + second;
  if (whole === 0) return 0;
  const sign = text[0] === "-" ? -1 : 1;
  const offset = sign * (whole + micro / 1e6);
  if (whole >= DAY_SECONDS) {
    const days = Math.floor(offset / DAY_SECONDS);
    const seconds = Math.floor(offset) - days * DAY_SECONDS;
    const fraction = sign < 0 && micro ? 1e6 - micro : micro;
    const fields = [`days=${days}`];
    if (seconds) fields.push(`seconds=${seconds}`);
    if (fraction) fields.push(`microseconds=${fraction}`);
    throw new RangeError("offset must be a timedelta strictly between -timedelta(hours=24) " +
      `and timedelta(hours=24), not datetime.timedelta(${fields.join(", ")}).`);
  }
  return offset;
}

function firstWeekMonday(year) {
  const january4 = wallSeconds({ year, month: 1, day: 4, hour: 0, minute: 0, second: 0 });
  const weekday = new Date(january4 * 1000).getUTCDay();
  return january4 - ((weekday + 6) % 7) * DAY_SECONDS;
}

function parseIsoDateTime(text) {
  const invalid = () => new RangeError(`Invalid isoformat string: ${pyRepr(text)}`);
  const match = DATE.exec(text);
  if (!match) throw invalid();
  let [, year, , month, day, , week, weekday = "1"] = match;
  year = Number(year);
  if (year === 0) throw new RangeError("year 0 is out of range");
  if (week) {
    const start = firstWeekMonday(year);
    const seconds = start + ((Number(week) - 1) * 7 + Number(weekday) - 1) * DAY_SECONDS;
    if (seconds < start || seconds >= firstWeekMonday(year + 1) || seconds > MAX_SECOND) {
      throw invalid();
    }
    ({ year, month, day } = utcParts(seconds));
  } else {
    month = Number(month);
    day = Number(day);
  }

  let hour = 0, minute = 0, second = 0, micro = 0, offsetSeconds = null;
  const suffix = text.slice(match[0].length);
  if (suffix) {
    const clock = [...suffix].slice(1).join("");
    const time = /^([^+\-Zz]+)([Zz]|[+-].+)?$/su.exec(clock);
    if (!time || time[0] !== clock) throw invalid();
    [hour, minute, second, micro] = parseClock(time[1], invalid);
    offsetSeconds = parseOffset(time[2], invalid);
  }

  const wall = { year, month, day, hour, minute, second, micro };
  if (month < 1 || month > 12) throw new RangeError("month must be in 1..12");
  if (day < 1 || utcParts(wallSeconds({ ...wall, hour: 0, minute: 0, second: 0 })).day !== day) {
    throw new RangeError("day is out of range for month");
  }
  if (hour > 23) throw new RangeError("hour must be in 0..23");
  if (minute > 59) throw new RangeError("minute must be in 0..59");
  if (second > 59) throw new RangeError("second must be in 0..59");
  return { wall, offsetSeconds };
}

// tz.js uses Date.UTC for offsets, which treats years 0–99 as 1900–1999.
// IANA zones use their initial offset before their first transition.
const EARLY_SAMPLE = wallSeconds({ year: 101, month: 1, day: 1, hour: 0, minute: 0, second: 0 });
function zoneOffset(zone, seconds) {
  return offsetSecondsAt(zone, Math.max(seconds, EARLY_SAMPLE));
}

function localParts(zone, seconds) {
  return seconds < EARLY_SAMPLE ? utcParts(seconds + zoneOffset(zone, seconds)) : partsAt(zone, seconds);
}

function localCandidates(zone, wall) {
  const seconds = wallSeconds(wall);
  if (seconds < EARLY_SAMPLE) return [seconds - zoneOffset(zone, seconds)];
  return wallCandidates(zone, wall);
}

function isoToEpochMicro({ wall, offsetSeconds }, zone, usePostGapOffset = false) {
  const seconds = wallSeconds(wall);
  if (offsetSeconds !== null) {
    return BigInt(seconds) * MICROS_PER_SECOND + BigInt(wall.micro) -
      BigInt(Math.round(offsetSeconds * 1e6));
  }

  let instant;
  if (seconds < EARLY_SAMPLE) {
    instant = seconds - zoneOffset(zone, seconds);
  } else {
    instant = wallToInstantFold0(zone, wall);
    if (wallSeconds(localParts(zone, instant)) !== seconds) {
      // An imaginary wall time has no round trip. Explicit zones use the
      // pre-transition offset, while Python's naive astimezone uses the later offset.
      const sample = seconds + (usePostGapOffset ? 2 : -2) * DAY_SECONDS;
      instant = seconds - zoneOffset(zone, sample);
    }
  }
  return BigInt(instant) * MICROS_PER_SECOND + BigInt(wall.micro);
}

function floorSeconds(epochMicro) {
  const quotient = epochMicro / MICROS_PER_SECOND;
  return Number(epochMicro % MICROS_PER_SECOND < 0n ? quotient - 1n : quotient);
}

function isIanaZone(zone) {
  // Newer Intl versions also accept bare numeric UTC offsets.
  return isValidZone(zone) && !/^[+-]/.test(zone);
}

module.exports = {
  isIanaZone, parseIsoDateTime, isoToEpochMicro, floorSeconds, utcParts, localParts, localCandidates,
  zoneOffset, MICROS_PER_SECOND, MIN_SECOND, MAX_SECOND, DAY_SECONDS, EARLY_SAMPLE,
};
