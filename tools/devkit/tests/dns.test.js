"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { createRun } = require("../commands/dns");
const { runCli } = require("./helpers");

const mx = [{ priority: 10, exchange: "mail.example.test" }];

function fail(code) {
  return async () => { throw Object.assign(new Error(code), { code }); };
}

async function runDns(args, overrides = {}) {
  const run = createRun({
    resolve4: async (host) => {
      assert.equal(host, "example.test");
      return ["192.0.2.1"];
    },
    resolve6: fail("ENODATA"),
    resolveCname: fail("ENOTFOUND"),
    resolveMx: async () => mx,
    resolveTxt: async () => [["first", "second"], ["other"]],
    resolveNs: async () => [],
    ...overrides,
  });
  let stdout = "";
  let stderr = "";
  const status = await run(["example.test", ...args], {
    out: (value) => { stdout += value; },
    err: (value) => { stderr += value; },
  });
  return { status, stdout, stderr };
}

test("dns ALL omits absent records and joins TXT chunks", async () => {
  const result = await runDns(["--json"]);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    host: "example.test", records: { A: ["192.0.2.1"], MX: mx, TXT: ["firstsecond", "other"] },
  });
  const text = await runDns(["--type", "MX"]);
  assert.deepEqual(text, { status: 0, stdout: "MX:\n10 mail.example.test\n", stderr: "" });
});

test("dns reports empty results and resolver errors", async () => {
  const empty = await runDns([], { resolve4: fail("ENOTFOUND"), resolveMx: async () => [], resolveTxt: async () => [] });
  assert.equal(empty.status, 1);
  assert.match(empty.stderr, /レコードがありません/);
  const missing = await runDns(["--type", "AAAA"]);
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /AAAA.*ENODATA/);
  const failed = await runDns([], { resolve4: fail("SERVFAIL") });
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /SERVFAIL/);
});

test("dns rejects invalid types and missing hosts before resolving", () => {
  assert.equal(runCli(["dns", "example.test", "--type", "PTR"]).status, 2);
  assert.equal(runCli(["dns"]).status, 2);
});
