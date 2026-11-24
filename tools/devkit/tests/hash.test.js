"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("hash computes SHA-256 and verifies an HMAC from stdin", () => {
  const hash = runCli(["hash", "abc"]);
  assert.equal(hash.status, 0, hash.stderr);
  assert.equal(hash.stdout.trim(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  const digest = "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8";
  const result = runCli(["hash", "--hmac", "key", "--check", digest.toUpperCase(), "--json"], {
    input: "The quick brown fox jumps over the lazy dog",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { algo: "sha256", hmac: true, digest, match: true });
});

test("hash reports mismatches and rejects unsupported algorithms", () => {
  const mismatch = runCli(["hash", "--check", "00", "--json", "abc"]);
  assert.equal(mismatch.status, 1);
  assert.equal(JSON.parse(mismatch.stdout).match, false);
  assert.equal(runCli(["hash", "--algo", "unknown", "abc"]).status, 2);
});
