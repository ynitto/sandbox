"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("pw includes each enabled character class and honors exclusions", () => {
  const result = runCli(["pw", "--length", "20", "--count", "3", "--no-symbols", "--no-ambiguous"]);
  assert.equal(result.status, 0, result.stderr);
  const passwords = result.stdout.trimEnd().split("\n");
  assert.equal(passwords.length, 3);
  for (const password of passwords) {
    assert.match(password, /^[A-Za-z0-9]{20}$/);
    assert.doesNotMatch(password, /[0O1lI]/);
    assert.match(password, /[a-z]/);
    assert.match(password, /[A-Z]/);
    assert.match(password, /[0-9]/);
  }
});

test("pw rejects lengths below the enabled character class count", () => {
  assert.equal(runCli(["pw", "--length", "3"]).status, 2);
});
