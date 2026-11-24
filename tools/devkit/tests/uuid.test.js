"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runCli } = require("./helpers");

test("uuid generates uppercase UUID v4 values and validates an existing value", () => {
  const generated = runCli(["uuid", "--count", "3", "--upper", "--json"]);
  assert.equal(generated.status, 0, generated.stderr);
  const uuids = JSON.parse(generated.stdout);
  assert.equal(uuids.length, 3);
  for (const uuid of uuids) assert.match(uuid, /^[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$/);
  const checked = runCli(["uuid", "--json", uuids[0]]);
  assert.equal(checked.status, 0, checked.stderr);
  assert.deepEqual(JSON.parse(checked.stdout), { uuid: uuids[0], valid: true, version: 4, variant: "RFC 4122" });
});

test("uuid rejects malformed input and invalid counts", () => {
  assert.equal(runCli(["uuid", "invalid"]).status, 2);
  assert.equal(runCli(["uuid", "--count", "0"]).status, 2);
  assert.equal(runCli(["uuid", "--count", "2", "550e8400-e29b-41d4-a716-446655440000"]).status, 2);
});
