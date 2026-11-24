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
});
