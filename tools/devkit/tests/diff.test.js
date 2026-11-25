"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");

test("diff emits unified hunks with context and handles equality and bad files", (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-diff-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(join(cwd, "old.txt"), "a\nb\nc\nd\ne\n");
  fs.writeFileSync(join(cwd, "new.txt"), "x\nb\nc\nd\ny\n");
  const result = runCli(["diff", "--context", "1", "old.txt", "new.txt"], { cwd });
  assert.equal(result.status, 1, result.stderr);
  assert.equal(result.stdout, "--- a/old.txt\n+++ b/new.txt\n@@ -1,2 +1,2 @@\n-a\n+x\n b\n@@ -4,2 +4,2 @@\n d\n-e\n+y\n");
  const equal = runCli(["diff", "old.txt", "-"], { cwd, input: "a\nb\nc\nd\ne\n" });
  assert.equal(equal.status, 0, equal.stderr);
  assert.equal(equal.stdout, "");
  assert.equal(runCli(["diff", "old.txt", "missing"], { cwd }).status, 2);
});

test("diff reports zero-length ranges and missing final newlines", (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-diff-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(join(cwd, "empty"), "");
  const result = runCli(["diff", "-", "empty"], { cwd, input: "one" });
  assert.equal(result.status, 1, result.stderr);
  assert.equal(result.stdout, "--- a/-\n+++ b/empty\n@@ -1 +0,0 @@\n-one\n\\ No newline at end of file\n");
});

test("diff refuses a middle section too large for the LCS table", (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-diff-large-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const lines = (prefix) => Array.from({ length: 5000 }, (_, i) => `${prefix}${i}`).join("\n") + "\n";
  fs.writeFileSync(join(cwd, "a.txt"), lines("a"));
  fs.writeFileSync(join(cwd, "b.txt"), lines("b"));
  assert.equal(runCli(["diff", "a.txt", "b.txt"], { cwd }).status, 2);
});
