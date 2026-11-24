"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("ulid generates IDs with a fixed timestamp and decodes them", () => {
  const time = "2024-03-12T01:23:45.678Z";
  const generated = runCli(["ulid", "--time", time, "--count", "3", "--json"]);
  assert.equal(generated.status, 0, generated.stderr);
  const ids = JSON.parse(generated.stdout);
  assert.equal(new Set(ids).size, 3);
  for (const ulid of ids) {
    assert.match(ulid, /^[0-7][0-9A-HJKMNP-TV-Z]{25}$/);
    const result = runCli(["ulid", ulid.toLowerCase(), "--json"]);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), { ulid: ulid.toLowerCase(), timestamp: Date.parse(time), iso: time });
  }
  assert.match(runCli(["ulid", "--time", "0"]).stdout, /^0{10}/);
});

test("ulid rejects invalid alphabets, overflow and invalid timestamps", () => {
  for (const args of [["8" + "0".repeat(25)], ["0".repeat(25) + "I"], ["123"], ["--time", "281474976710656"],
    ["--time", "2024-02-30"], ["--count", "0"]]) {
    assert.equal(runCli(["ulid", ...args]).status, 2);
  }
});

test("ulid decodes a known timestamp from its Crockford base32 prefix", () => {
  // 先頭10文字 01ARZ3NDEK は 1468930850259 ms を表す（基数変換を手計算で検証済み）。
  const result = runCli(["ulid", "01ARZ3NDEKTSV4RRFFQ69G5FAV"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /2016-07-30T23:54:10\.259Z/);
});
