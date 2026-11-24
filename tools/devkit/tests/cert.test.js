"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const tls = require("node:tls");
const { runCli, runCliAsync, listen, close } = require("./helpers");

test("cert reads a local certificate and warns when below the requested threshold", async () => {
  const server = tls.createServer({
    cert: readFileSync(join(__dirname, "fixtures", "cert.pem")),
    key: readFileSync(join(__dirname, "fixtures", "key.pem")),
  }, (socket) => socket.end());
  const port = await listen(server);
  const args = ["cert", "127.0.0.1", "--port", String(port)];
  try {
    const start = Date.now();
    const result = await runCliAsync([...args, "--json"]);
    assert.equal(result.status, 0, result.stderr);
    const data = JSON.parse(result.stdout);
    assert.equal(data.subject.CN, "localhost");
    assert.equal(data.issuer.CN, "localhost");
    assert.match(data.valid_to, /2126/);
    const expiry = Date.parse(data.valid_to);
    assert.ok(data.days_remaining >= Math.floor((expiry - Date.now()) / 86400000));
    assert.ok(data.days_remaining <= Math.floor((expiry - start) / 86400000));
    assert.match(data.subjectaltname, /DNS:localhost/);
    assert.match(data.subjectaltname, /IP Address:127\.0\.0\.1/);
    assert.equal(data.authorized, false);
    assert.equal(typeof data.authorization_error, "string");
    assert.ok(data.authorization_error.length > 0);
    const warning = await runCliAsync([...args, "--warn-days", "100000"]);
    assert.equal(warning.status, 1);
    for (const key of ["subject", "issuer", "valid_from", "valid_to", "days_remaining", "subjectaltname"]) {
      assert.ok(warning.stdout.split("\n").some((line) => line.startsWith(key + ": ")), key);
    }
    assert.match(warning.stderr, /警告/);
  } finally {
    await close(server);
  }
  const failed = await runCliAsync(args);
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /ECONNREFUSED/);
});

test("cert rejects invalid options", () => {
  for (const args of [["--port", "65536"], ["--warn-days", "-1"], ["--warn-days", "1.5"]]) {
    assert.equal(runCli(["cert", "127.0.0.1", ...args]).status, 2);
  }
  assert.equal(runCli(["cert"]).status, 2);
});
