"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli, runCliBuffer } = require("./helpers");

test("b32 follows RFC 4648 and accepts lowercase and missing padding", () => {
  const vectors = [["f", "MY======"], ["fo", "MZXQ===="], ["foo", "MZXW6==="], ["foob", "MZXW6YQ="],
    ["fooba", "MZXW6YTB"], ["foobar", "MZXW6YTBOI======"]];
  for (const [clear, encoded] of vectors) {
    const result = runCli(["b32", "--file", "missing", clear], { input: "ignored" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, encoded + "\n");
    assert.equal(runCli(["b32", "--decode"], { input: encoded.toLowerCase().replace(/=+$/, "") }).stdout, clear);
  }
});

test("b32 preserves leading zero bytes and rejects malformed encodings", () => {
  const bytes = Buffer.from([0, 0, 255]);
  for (const [format, text] of [["base58", "115Q"], ["hex", "0000ff"]]) {
    assert.equal(runCliBuffer(["b32", "--format", format], { input: bytes }).stdout.toString(), text + "\n");
    assert.deepEqual(runCliBuffer(["b32", "--format", format, "--decode", text]).stdout, bytes);
  }
  for (const [format, input] of [["base32", "A"], ["base32", "MY="], ["base32", "MZ"], ["base58", "0"], ["hex", "abc"], ["hex", "00x0"]]) {
    assert.equal(runCli(["b32", "--format", format, "--decode", input]).status, 2);
  }
});
