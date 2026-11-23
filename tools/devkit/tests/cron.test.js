"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { runCli } = require("./helpers");

function run(expression, start, count = 1, zone = "UTC") {
  return runCli(["cron", expression, "--from", start, "--count", String(count), "--tz", zone]);
}

function assertRuns(expression, start, expected, zone) {
  const result = run(expression, start, expected.length, zone);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trimEnd().split("\n"), expected);
}

test("quarter hour excludes start", () => {
  assertRuns("*/15 * * * *", "2026-11-23T00:15:00", [
    "2026-11-23 00:30 (Mon)", "2026-11-23 00:45 (Mon)",
  ]);
});

test("weekdays across weekend", () => {
  assertRuns("0 9 * * MON-FRI", "2026-11-27T09:00:01", [
    "2026-11-30 09:00 (Mon)", "2026-12-01 09:00 (Tue)",
  ]);
});

test("monthly", () => {
  assertRuns("0 0 1 * *", "2026-11-23", [
    "2026-12-01 00:00 (Tue)", "2027-01-01 00:00 (Fri)",
  ]);
});

test("yearly", () => {
  assertRuns("0 0 1 1 *", "2026-11-23", [
    "2027-01-01 00:00 (Fri)", "2028-01-01 00:00 (Sat)",
  ]);
});

test("leap day across a non-leap century", () => {
  assertRuns("0 0 29 2 *", "2096-02-29", ["2104-02-29 00:00 (Fri)"]);
});

test("day or weekday", () => {
  assertRuns("0 0 1 * MON", "2026-11-29", [
    "2026-11-30 00:00 (Mon)", "2026-12-01 00:00 (Tue)",
  ]);
});

test("names, lists, ranges and steps", () => {
  assertRuns("0,10-30/10 8 */2 jAn Sun,7", "2027-01-02", [
    "2027-01-03 08:00 (Sun)", "2027-01-03 08:10 (Sun)",
    "2027-01-03 08:20 (Sun)", "2027-01-03 08:30 (Sun)",
    "2027-01-17 08:00 (Sun)",
  ]);
});

test("time zone with offset in start", () => {
  assertRuns("30 7 * * *", "2026-11-22T22:30:00Z", [
    "2026-11-24 07:30 (Tue)",
  ], "Asia/Tokyo");
});

test("DST gap and fold", () => {
  const zone = "America/New_York";
  assertRuns("30 2 * * *", "2026-03-07T02:30", ["2026-03-09 02:30 (Mon)"], zone);
  assertRuns("30 1 * * *", "2026-11-01T00:00", [
    "2026-11-01 01:30 (Sun)", "2026-11-01 01:30 (Sun)", "2026-11-02 01:30 (Mon)",
  ], zone);
});

for (const [expression, reason] of [
  ["* * * *", "5フィールド"],
  ["60 * * * *", "分フィールド"],
  ["0 0 * * 8", "範囲外"],
  ["*/0 * * * *", "ステップ"],
  ["0 0 * Foo *", "無効な値または名前"],
]) {
  test(`invalid expression: ${expression}`, () => {
    const result = run(expression, "2026-11-23");
    assert.equal(result.status, 2);
    assert.ok(result.stderr.includes(reason), result.stderr);
    assert.equal(result.stdout, "");
  });
}

test("full range counts as restricted", () => {
  assertRuns("0 9 1-31 * MON", "2026-11-23T00:00", [
    "2026-11-23 09:00 (Mon)", "2026-11-24 09:00 (Tue)",
  ]);
});

test("star step counts as star", () => {
  assertRuns("0 9 */1 * MON", "2026-11-23T00:00", [
    "2026-11-23 09:00 (Mon)", "2026-11-30 09:00 (Mon)",
  ]);
});

test("invalid options", () => {
  const invalidZone = run("0 9 * * *", "2026-11-23", 1, "No/Such_Zone");
  assert.equal(invalidZone.status, 2);
  assert.ok(invalidZone.stderr.includes("タイムゾーン"));
  const invalidCount = run("0 9 * * *", "2026-11-23", 0);
  assert.equal(invalidCount.status, 2);
  assert.ok(invalidCount.stderr.includes("--count"));
});

for (const expression of ["0 0 31 2 *", "0 0 31 4 *"]) {
  test(`nonexistent dates: ${expression}`, () => {
    const result = run(expression, "2026-11-23");
    assert.equal(result.status, 1);
    assert.ok(result.stderr.includes("見つかりません"));
    assert.equal(result.stdout, "");
  });
}

test("a nonexistent start uses the offset before the DST transition", () => {
  assertRuns("0 3,4 * * *", "2026-03-08T02:30", ["2026-03-08 04:00 (Sun)"], "America/New_York");
});
