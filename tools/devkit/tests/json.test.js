"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("json formats stdin and extracts values by array index", () => {
  const formatted = runCli(["json", "--indent", "4"], { input: '{"id":1}' });
  assert.equal(formatted.status, 0, formatted.stderr);
  assert.equal(formatted.stdout, '{\n    "id": 1\n}\n');
  const extracted = runCli(["json", "--path", "items.0.name", '{"items":[{"name":"sample"}]}']);
  assert.equal(extracted.status, 0, extracted.stderr);
  assert.equal(extracted.stdout, "sample\n");
});

test("json distinguishes missing paths from invalid JSON", () => {
  const missing = runCli(["json", "--path", "missing", "{}"]);
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /見つかりません/);
  const invalid = runCli(["json", "--check", "{"]);
  assert.equal(invalid.status, 2);
  assert.match(invalid.stderr, /不正な JSON/);
});
