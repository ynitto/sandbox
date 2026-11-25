"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("lines deduplicates and counts before stable numeric sorting and reversal", () => {
  const result = runCli(["lines", "--uniq", "--count", "--sort", "--numeric", "--reverse"], {
    input: "3\r\n1\r\nz\n02\n2\n3\na\n",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "1\tz\n1\ta\n2\t3\n1\t2\n1\t02\n1\t1\n");
  assert.equal(runCli(["lines"], { input: "" }).stdout, "");
  assert.equal(runCli(["lines"], { input: "\n\n" }).stdout, "\n\n");
});

test("lines shuffles the given rows and rejects incompatible options", () => {
  const result = runCli(["lines", "--shuffle"], { input: "a\nb\na\nc" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trimEnd().split("\n").sort(), ["a", "a", "b", "c"]);
  assert.equal(runCli(["lines", "--shuffle", "--reverse"]).status, 2);
  // 行数が多ければ、シャッフル結果が入力順のままであることはまず起きない。
  const many = Array.from({ length: 20 }, (_, i) => `row${i}`).join("\n");
  const shuffled = runCli(["lines", "--shuffle"], { input: many });
  assert.equal(shuffled.status, 0, shuffled.stderr);
  assert.notEqual(shuffled.stdout, many + "\n");
  assert.deepEqual(shuffled.stdout.trimEnd().split("\n").sort(), many.split("\n").sort());
});

test("lines treats --numeric as sorting and --count as deduplication", () => {
  const sorted = runCli(["lines", "--numeric"], { input: "10\n2\n1\n" });
  assert.equal(sorted.status, 0, sorted.stderr);
  assert.equal(sorted.stdout, "1\n2\n10\n");
  const counted = runCli(["lines", "--count"], { input: "b\na\nb\n" });
  assert.equal(counted.status, 0, counted.stderr);
  assert.equal(counted.stdout, "2\tb\n1\ta\n");
  assert.equal(runCli(["lines", "--numeric", "--shuffle"], { input: "1\n2\n" }).status, 2);
});
