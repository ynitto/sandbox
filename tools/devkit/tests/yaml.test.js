"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("yaml parses nested maps, list maps, flow collections, comments and quotes", () => {
  const input = [
    "service:", "  items:", '    - name: "a # b" # comment', "      enabled: true",
    "      values: [1, ~, {key: 'it''s fine', data: [false, 2.5]}]",
    '    - name: "a\\nb"', "      enabled: false", "  empty: null",
  ].join("\n");
  const result = runCli(["yaml"], { input });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    service: { items: [{ name: "a # b", enabled: true, values: [1, null, { key: "it's fine", data: [false, 2.5] }] },
      { name: "a\nb", enabled: false }], empty: null },
  });
  assert.match(result.stdout, /\n  "service"/);
});

test("yaml formats JSON with two spaces and round-trips ambiguous strings", () => {
  const value = { list: ["true", "001", "null", "a:b", "#comment", "", {}, [], ["a"]], "a:b": { ok: "yes" }, name: "Alice" };
  const formatted = runCli(["yaml"], { input: JSON.stringify(value) });
  assert.equal(formatted.status, 0, formatted.stderr);
  assert.match(formatted.stdout, /list:\n  - "true"/);
  assert.match(formatted.stdout, /\nname: Alice\n/);
  const result = runCli(["yaml", "--to", "json"], { input: formatted.stdout });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), value);
});

test("yaml rejects unsupported syntax and reports its line", () => {
  for (const value of ["&anchor 1", "*anchor", "|", ">-", "!!str name", "[*anchor]", "{a: !!str b}"]) {
    const result = runCli(["yaml"], { input: `ok: true\ninvalid: ${value}\n` });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /2 行目: 対応していない記法です/);
  }
  assert.match(runCli(["yaml"], { input: "a: 1\n---\na: 2\n" }).stderr, /2 行目: 対応していない記法です/);
});

test("yaml keeps leading-zero and unsafe integers as strings", () => {
  const result = runCli(["yaml"], { input: "code: 01\nid: 9007199254740993\nzero: 0\n" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { code: "01", id: "9007199254740993", zero: 0 });
});

test("yaml rejects excessively deep nesting instead of overflowing", () => {
  const flow = "value: " + "[".repeat(150) + "1" + "]".repeat(150) + "\n";
  assert.equal(runCli(["yaml"], { input: flow }).status, 2);
  const blocks = Array.from({ length: 150 }, (_, i) => " ".repeat(i) + (i % 2 ? "- x" : `k${i}:`)).join("\n") + "\n";
  assert.equal(runCli(["yaml"], { input: blocks }).status, 2);
});

test("yaml --to yaml normalizes YAML input", () => {
  const result = runCli(["yaml", "--to", "yaml"], { input: "name: test\nitems:\n  - 1\n  - 2\n" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /name: test/);
  const back = runCli(["yaml"], { input: result.stdout });
  assert.equal(back.status, 0, back.stderr);
  assert.deepEqual(JSON.parse(back.stdout), { name: "test", items: [1, 2] });
});
