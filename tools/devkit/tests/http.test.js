"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const http = require("node:http");
const { EventEmitter } = require("node:events");
const { runCommand } = require("..");
const { runCli, runCliAsync, listen, close } = require("./helpers");

test("http reports the final response and discards the body", async (t) => {
  const server = http.createServer((req, res) => {
    res.setHeader("X-Method", req.method);
    res.end("response body");
  });
  const port = await listen(server);
  t.after(() => close(server));
  const url = "http://127.0.0.1:" + port + "/";
  const result = await runCliAsync(["http", url]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^200 OK\ntime: \d+ ms\n/);
  assert.match(result.stdout, /X-Method: GET/);
  assert.doesNotMatch(result.stdout, /response body/);
});

test("http follows relative redirects and switches or preserves the method", async (t) => {
  const methods = [];
  const server = http.createServer((req, res) => {
    if (req.url === "/switch") methods.push(req.method);
    if (req.url === "/start") res.writeHead(307, { Location: "/switch" });
    else if (req.url === "/switch") res.writeHead(302, { Location: "final" });
    else res.writeHead(200, { "X-Method": req.method });
    res.end();
  });
  const port = await listen(server);
  t.after(() => close(server));
  const base = "http://127.0.0.1:" + port;
  const result = await runCliAsync(["http", base + "/start", "--method", "POST", "--json"]);
  assert.equal(result.status, 0, result.stderr);
  const data = JSON.parse(result.stdout);
  assert.equal(data.url, base + "/final");
  assert.equal(data.status, 200);
  assert.equal(data.statusText, "OK");
  assert.ok(data.timeMs >= 0);
  assert.deepEqual(data.redirects, [
    { status: 307, location: base + "/switch" },
    { status: 302, location: base + "/final" },
  ]);
  assert.equal(data.headers["x-method"], "GET");
  const stopped = await runCliAsync(["http", base + "/start", "--no-follow", "--json"]);
  assert.equal(stopped.status, 0, stopped.stderr);
  assert.equal(JSON.parse(stopped.stdout).status, 307);
  assert.deepEqual(JSON.parse(stopped.stdout).redirects, []);
  const preserved = await runCliAsync(["http", base + "/start", "--method", "POST"]);
  assert.equal(preserved.status, 0, preserved.stderr);
  assert.ok(preserved.stdout.startsWith("307 " + base + "/start -> " + base + "/switch\n"));
  assert.deepEqual(methods, ["POST", "POST"]);
});

test("http preserves HEAD across redirects", async (t) => {
  const methods = [];
  const redirects = new Map([
    ["/start", [301, "/second"]],
    ["/second", [302, "/third"]],
    ["/third", [303, "/final"]],
  ]);
  t.mock.method(http, "request", (url, { method }, onResponse) => {
    methods.push(method);
    const [statusCode, location] = redirects.get(url.pathname) ?? [200];
    const response = Object.assign(new EventEmitter(), {
      statusCode,
      headers: { location },
      resume() { this.emit("end"); },
    });
    const request = new EventEmitter();
    request.end = () => onResponse(response);
    return request;
  });
  const result = await runCommand("http", ["--method", "HEAD", "--json", "http://example.test/start"]);
  assert.equal(result.code, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).status, 200);
  assert.deepEqual(methods, ["HEAD", "HEAD", "HEAD", "HEAD"]);
});

test("http reports connection failures, timeouts, and redirect loops", async (t) => {
  const server = http.createServer((req, res) => {
    if (req.url === "/loop") res.writeHead(301, { Location: "/loop" }).end();
  });
  const port = await listen(server);
  t.after(() => close(server));
  const base = "http://127.0.0.1:" + port;
  const timeout = await runCliAsync(["http", base + "/timeout", "--timeout", "50"]);
  assert.equal(timeout.status, 1);
  assert.match(timeout.stderr, /タイムアウト/);
  const loop = await runCliAsync(["http", base + "/loop"]);
  assert.equal(loop.status, 1);
  assert.match(loop.stderr, /上限/);
  assert.equal(loop.stdout.trimEnd().split("\n").length, 10);
  const closedServer = http.createServer();
  const closedPort = await listen(closedServer);
  await close(closedServer);
  const failed = await runCliAsync(["http", "http://127.0.0.1:" + closedPort]);
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /ECONNREFUSED/);
});

test("http rejects invalid URLs and unsupported schemes", () => {
  for (const url of ["broken", "ftp://example.test"]) assert.equal(runCli(["http", url]).status, 2);
});

test("http exits 0 for a 404 response (only connection errors exit 1)", async (t) => {
  const server = http.createServer((req, res) => {
    res.writeHead(404);
    res.end();
  });
  const port = await listen(server);
  t.after(() => close(server));
  const result = await runCliAsync(["http", "http://127.0.0.1:" + port + "/missing"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^404 /);
});

test("http uses https and reports certificate errors for untrusted servers", async (t) => {
  const { readFileSync } = require("node:fs");
  const { join } = require("node:path");
  const tls = require("node:tls");
  const server = tls.createServer({
    cert: readFileSync(join(__dirname, "fixtures", "cert.pem")),
    key: readFileSync(join(__dirname, "fixtures", "key.pem")),
  }, (socket) => socket.end());
  const port = await listen(server);
  t.after(() => close(server));
  const result = await runCliAsync(["http", "https://127.0.0.1:" + port + "/"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /certificate|証明書/i);
});
