"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { runCli } = require("./helpers");

function run(args, input = "") {
  return runCli(["ts", ...args], { input });
}

for (const sign of ["", "-"]) {
  test(`auto threshold: ${sign || "+"}`, () => {
    const result = run(["--tz", "UTC", "--json",
      sign + "1000000000000", sign + "999999999999.999999", sign + "1000000000001"]);
    assert.equal(result.status, 2);
    const factor = sign ? -1 : 1;
    assert.deepEqual(JSON.parse(result.stdout).map((output) => output.epoch_ms),
      [factor * 1000000000000, factor * 1000000000001]);
    assert.ok(result.stderr.includes(sign + "999999999999.999999"));
  });
}

for (const [flags, value, seconds] of [
  [[], "1700000000.125", 1700000000.125],
  [["--unit", "ms"], "-1500.", -1.5],
  [["--unit", "s"], "1700000000", 1700000000],
  [["--unit", "ms"], "999999999999", 999999999.999],
]) {
  test(`auto seconds and explicit units: ${flags.join(" ")} ${value}`, () => {
    const result = run(["--tz", "UTC", "--json", ...flags, value]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).epoch_s, seconds);
  });
}

test("ISO dates and time zone", () => {
  const result = run(["--tz", "Asia/Tokyo", "--json", "1970-01-01T00:00:00Z",
    "1970-01-01T02:30:00+02:30", "1970-01-01 09:00:00", "1970-01-01"]);
  assert.equal(result.status, 0, result.stderr);
  const [first, second, third, date] = JSON.parse(result.stdout);
  for (const output of [first, second, third]) {
    assert.equal(output.epoch_s, 0);
    assert.equal(output.epoch_ms, 0);
    assert.equal(output.utc, "1970-01-01T00:00:00+00:00");
    assert.equal(output.local, "1970-01-01T09:00:00+09:00");
  }
  assert.equal(date.epoch_s, -32400);
});

test("lowercase z suffix", () => {
  const result = run(["--tz", "UTC", "--json", "1970-01-01T00:00:00z"]);
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout);
  assert.equal(output.epoch_s, 0);
  assert.equal(output.utc, "1970-01-01T00:00:00+00:00");
});

test("text output", () => {
  const result = run(["0", "--tz", "Asia/Tokyo", "1970-01-01T00:00:00Z"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trimEnd().split("\n"), [
    "Asia/Tokyo: 1970-01-01T09:00:00+09:00",
    "UTC: 1970-01-01T00:00:00+00:00",
    "Epoch (s): 0",
    "Epoch (ms): 0",
    "",
    "Epoch (s): 0",
    "Epoch (ms): 0",
    "UTC: 1970-01-01T00:00:00+00:00",
    "Asia/Tokyo: 1970-01-01T09:00:00+09:00",
  ]);
});

for (const [args, input, extra] of [[[], "", false], [["0"], "", true], [[], "0\n", true]]) {
  test(`now with args=${JSON.stringify(args)} and stdin=${JSON.stringify(input)}`, () => {
    const before = Date.now();
    const result = run(["--now", "--json", "--tz", "UTC", ...args], input);
    const after = Date.now();
    assert.equal(result.status, 0, result.stderr);
    let output = JSON.parse(result.stdout);
    if (extra) {
      const [now, other] = output;
      assert.equal(other.epoch_s, 0);
      output = now;
    }
    assert.equal(output.input, "now");
    assert.match(output.utc, /\+00:00$/);
    const timestamp = Date.parse(output.utc);
    assert.ok(before <= timestamp && timestamp <= after);
  });
}

for (const args of [
  [], ["bad"], ["--tz", "Invalid/Zone", "0"], ["--tz", "", "0"], ["--unit", "hours", "0"],
]) {
  test(`input errors: ${JSON.stringify(args)}`, () => {
    const result = run(args);
    assert.equal(result.status, 2);
    assert.ok(result.stderr);
  });
}

test("stdin batch", () => {
  const result = run(["--tz", "UTC", "--json"], "0\n\n 1970-01-02T00:00:00Z \n");
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).map((item) => item.epoch_s), [0, 86400]);
});

test("stdin partial failure", () => {
  const result = run(["--tz", "UTC", "--json"], "invalid\n \n0\n");
  assert.equal(result.status, 1);
  assert.ok(result.stderr.includes("invalid"));
  assert.deepEqual(JSON.parse(result.stdout).map((item) => item.epoch_s), [0]);
});

test("ties round to even on both sides of the epoch", () => {
  const result = run(["--tz", "UTC", "--json", ".0000005", "-.0000005", ".0000015", "-.0000015"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).map((item) => item.epoch_s), [0, 0, 0.000002, -0.000002]);
});

test("datetime boundaries and historical offsets", () => {
  const range = run(["--tz", "UTC", "--json", "0001-01-01", "9999-12-31T23:59:59.999999Z",
    "0001-01-01T00:00:00+00:00:01"]);
  assert.equal(range.status, 2);
  assert.match(range.stderr, /date value out of range/);
  assert.deepEqual(JSON.parse(range.stdout).map((item) => item.utc), [
    "0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59.999999+00:00",
  ]);

  const historical = run(["--tz", "Asia/Tokyo", "--json", "0001-01-02"]);
  assert.equal(historical.status, 0, historical.stderr);
  assert.equal(JSON.parse(historical.stdout).local, "0001-01-02T00:00:01+09:19");
});
