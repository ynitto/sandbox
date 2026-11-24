"use strict";

// Intl を使った IANA タイムゾーン処理。
// Python の zoneinfo + datetime の挙動（isoformat 表記、fold=0 の解釈、
// 夏時間のギャップ・重なりの検出）を再現する。

const FORMATTERS = new Map();

function formatter(zone) {
  let fmt = FORMATTERS.get(zone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    FORMATTERS.set(zone, fmt);
  }
  return fmt;
}

function isValidZone(zone) {
  if (typeof zone !== "string" || zone === "") return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

function localZoneName() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

// 指定した瞬間（epoch 秒）におけるゾーンの現地時刻部品。
function partsAt(zone, epochSeconds) {
  const parts = formatter(zone).formatToParts(new Date(epochSeconds * 1000));
  const result = {};
  for (const part of parts) {
    if (part.type !== "literal") result[part.type] = Number(part.value);
  }
  if (result.hour === 24) result.hour = 0;
  return result; // {year, month, day, hour, minute, second}
}

// 現地時刻部品を UTC として解釈した epoch 秒。
// Date.UTC は年 0〜99 を 1900〜1999 に読み替えるため、setUTCFullYear で組み立てる。
function wallAsUtcSeconds(wall) {
  const date = new Date(0);
  date.setUTCFullYear(wall.year, wall.month - 1, wall.day);
  date.setUTCHours(wall.hour, wall.minute, wall.second, 0);
  return date.getTime() / 1000;
}

// 指定した瞬間におけるゾーンの UTC オフセット（秒）。
function offsetSecondsAt(zone, epochSeconds) {
  const wall = partsAt(zone, epochSeconds);
  return wallAsUtcSeconds(wall) - epochSeconds;
}

function formatOffset(offsetSeconds, { withSeconds = null } = {}) {
  const sign = offsetSeconds < 0 ? "-" : "+";
  const abs = Math.abs(offsetSeconds);
  const hours = Math.floor(abs / 3600);
  const minutes = Math.floor((abs % 3600) / 60);
  const seconds = abs % 60;
  const base = `${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  if (withSeconds === true || (withSeconds === null && seconds !== 0)) {
    return `${base}:${String(seconds).padStart(2, "0")}`;
  }
  return base;
}

function pad(value, length) {
  return String(value).padStart(length, "0");
}

// Python の datetime.isoformat() 形式で整形する。
// epochMicro: BigInt のマイクロ秒。zone: IANA 名または "UTC"。
function formatIso(epochMicro, zone) {
  const totalSeconds = epochMicro / 1000000n;
  const micro = epochMicro - totalSeconds * 1000000n;
  // 負の時刻でも 0..999999 の剰余にする
  const seconds = micro < 0n ? totalSeconds - 1n : totalSeconds;
  const microPart = Number(micro < 0n ? micro + 1000000n : micro);
  const epochSeconds = Number(seconds);
  let wall;
  let offset;
  if (zone === "UTC" || zone === "Etc/UTC") {
    const date = new Date(epochSeconds * 1000);
    wall = {
      year: date.getUTCFullYear(),
      month: date.getUTCMonth() + 1,
      day: date.getUTCDate(),
      hour: date.getUTCHours(),
      minute: date.getUTCMinutes(),
      second: date.getUTCSeconds(),
    };
    offset = 0;
  } else {
    wall = partsAt(zone, epochSeconds);
    offset = offsetSecondsAt(zone, epochSeconds);
  }
  const fraction = microPart !== 0 ? "." + pad(microPart, 6) : "";
  return (
    `${pad(wall.year, 4)}-${pad(wall.month, 2)}-${pad(wall.day, 2)}` +
    `T${pad(wall.hour, 2)}:${pad(wall.minute, 2)}:${pad(wall.second, 2)}` +
    fraction +
    formatOffset(offset)
  );
}

function sameWall(a, b) {
  return (
    a.year === b.year &&
    a.month === b.month &&
    a.day === b.day &&
    a.hour === b.hour &&
    a.minute === b.minute &&
    a.second === b.second
  );
}

// 現地時刻（wall）に一致する瞬間（epoch 秒）の候補を昇順で返す。
// 夏時間のギャップでは空、重なりでは2件になる。
function wallCandidates(zone, wall) {
  const wallUtc = wallAsUtcSeconds(wall);
  const offsets = new Set();
  for (let delta = -48 * 3600; delta <= 48 * 3600; delta += 6 * 3600) {
    offsets.add(offsetSecondsAt(zone, wallUtc + delta));
  }
  const result = [];
  for (const offset of offsets) {
    const instant = wallUtc - offset;
    if (sameWall(partsAt(zone, instant), wall)) result.push(instant);
  }
  return [...new Set(result)].sort((a, b) => a - b);
}

// Python の replace(tzinfo=zone)（fold=0）と同じ解釈で現地時刻を瞬間に変換する。
// 重なりでは早い方、ギャップでは移行前のオフセットを使う。
function wallToInstantFold0(zone, wall) {
  const candidates = wallCandidates(zone, wall);
  if (candidates.length > 0) return candidates[0];
  const wallUtc = wallAsUtcSeconds(wall);
  // ギャップ: 前後どちらかのオフセットのうち、移行前（小さい方の瞬間側）を使う。
  // wallUtc - 2日 のオフセットは通常は移行前のもの。
  const beforeOffset = offsetSecondsAt(zone, wallUtc - 2 * 86400);
  const afterOffset = offsetSecondsAt(zone, wallUtc + 2 * 86400);
  // fold=0 は移行前のオフセット。オフセットが増える（春進み）場合は before が移行前。
  // どちらが移行前かは、候補が存在しない前提で「変換後にギャップ直前へ戻る」方を選ぶ。
  const instantBefore = wallUtc - beforeOffset;
  const instantAfter = wallUtc - afterOffset;
  // 移行前のオフセットで解釈した瞬間は、移行後のオフセットで解釈した瞬間より前に来る
  // のが春のギャップ。秋の戻りは candidates が2件になるためここには来ない。
  // 念のため、partsAt で得たオフセットが beforeOffset と一致する方を優先する。
  if (offsetSecondsAt(zone, instantBefore) === beforeOffset) return instantBefore;
  if (offsetSecondsAt(zone, instantAfter) === afterOffset) return instantAfter;
  return Math.min(instantBefore, instantAfter);
}

module.exports = {
  isValidZone,
  localZoneName,
  partsAt,
  wallAsUtcSeconds,
  offsetSecondsAt,
  formatOffset,
  formatIso,
  wallCandidates,
  wallToInstantFold0,
};
