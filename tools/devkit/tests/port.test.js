"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const net = require("node:net");
const { runCli, runCliAsync, listen, close } = require("./helpers");

test("port reports open and closed ports in argument order", async (t) => {
  const host = "127.0.0.1";
  const server = net.createServer((socket) => socket.end());
  const port = await listen(server);
  t.after(() => close(server));
  const unavailable = net.createServer();
  const closedPort = await listen(unavailable);
  await close(unavailable);
  const open = await runCliAsync(["port", host, "--port", String(port)]);
  assert.equal(open.status, 0, open.stderr);
  assert.equal(open.stdout, [host, port].join(":") + " open\n");
  const mixed = await runCliAsync(["port", host, "--json", "--port", [closedPort, port].join(",")]);
  assert.equal(mixed.status, 1);
  const records = JSON.parse(mixed.stdout);
  assert.deepEqual(records, [{ host, port: closedPort, open: false, error: records[0].error }, { host, port, open: true }]);
  assert.match(records[0].error, /ECONNREFUSED/);
  const closed = await runCliAsync(["port", host, "--port", String(closedPort)]);
  assert.equal(closed.status, 1);
  assert.match(closed.stdout, /closed \(.*ECONNREFUSED/);
});

test("port rejects missing or invalid port numbers", () => {
  assert.equal(runCli(["port", "127.0.0.1"]).status, 2);
  for (const port of ["0", "65536", "80,abc", "80,", "1.5"]) {
    assert.equal(runCli(["port", "127.0.0.1", "--port", port]).status, 2);
  }
});
