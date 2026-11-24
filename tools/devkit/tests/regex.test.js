"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("regex reports matches and captures and replaces all matches", () => {
  const result = runCli(["regex", "--flags", "i", "--replace", "<$1>", "--json", "(a)", "A a"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    pattern: "(a)", flags: "i", count: 2, replaced: "<A> <a>",
    matches: [{ match: "A", index: 0, groups: ["A"] }, { match: "a", index: 2, groups: ["a"] }],
  });
});

test("regex reports no matches and rejects invalid patterns and flags", () => {
  assert.equal(runCli(["regex", "x"], { input: "abc\n" }).status, 1);
  assert.equal(runCli(["regex", "[", "x"]).status, 2);
  assert.equal(runCli(["regex", "--flags", "g", "x", "x"]).status, 2);
});
