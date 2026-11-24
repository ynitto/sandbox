"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { runCli, runCliBuffer } = require("./helpers");

test("b64 prioritizes arguments over files and files over stdin", (t) => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-b64-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(path.join(cwd, "input"), Buffer.from([255, 254]));
  const options = { cwd, input: "stdin" };
  const positional = runCli(["b64", "--file", "input", "abc"], options);
  assert.equal(positional.status, 0, positional.stderr);
  assert.equal(positional.stdout, "YWJj\n");
  const file = runCli(["b64", "--url", "--file", "input"], options);
  assert.equal(file.status, 0, file.stderr);
  assert.equal(file.stdout, "__4=\n");
  const decoded = runCliBuffer(["b64", "--url", "--decode"], { input: file.stdout });
  assert.equal(decoded.status, 0);
  assert.deepEqual(decoded.stdout, Buffer.from([255, 254]));
});

test("b64 rejects invalid characters, padding, and missing files", () => {
  for (const args of [["--decode", "!"], ["--decode", "a==="], ["--decode", "a"], ["--file", "missing-base64-input"]]) {
    assert.equal(runCli(["b64", ...args]).status, 2);
  }
});
