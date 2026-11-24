"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { createInterface } = require("node:readline");
const test = require("node:test");
const { BIN, runCli } = require("./helpers");

function request(port, path, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: "localhost", port, path, method }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { text += chunk; });
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, text }));
      response.on("error", reject);
    });
    req.on("error", reject);
    req.setTimeout(3000, () => req.destroy(new Error("HTTP request timed out")));
    req.end();
  });
}

test("serve handles files, index, listing, HEAD and rejected paths", { timeout: 10000 }, async (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-serve-"));
  const root = join(cwd, "public");
  fs.mkdirSync(join(root, "sub"), { recursive: true });
  fs.writeFileSync(join(root, "index.html"), "<h1>home</h1>");
  fs.writeFileSync(join(root, "sub", "hello&.txt"), "hello\n");
  fs.mkdirSync(join(root, "sub", "nested"));
  fs.writeFileSync(join(cwd, "outside.txt"), "outside");
  if (process.platform !== "win32") fs.symlinkSync(join(cwd, "outside.txt"), join(root, "outside.txt"));

  const child = spawn(process.execPath, [BIN, "serve", "--port", "0", "--dir", "public"], { cwd });
  const closed = once(child, "close");
  t.after(async () => {
    child.kill();
    await closed;
    fs.rmSync(cwd, { recursive: true, force: true });
  });
  const stdout = createInterface({ input: child.stdout });
  let stderr = "";
  child.stderr.setEncoding("utf8").on("data", (chunk) => { stderr += chunk; });
  const [line] = await Promise.race([once(stdout, "line"), closed.then(([code]) => {
    throw new Error(`serve exited with ${code}: ${stderr}`);
  })]);
  const match = /^http:\/\/localhost:(\d+)\/ で配信中: (.+)$/.exec(line);
  assert.ok(match, line);
  assert.equal(match[2], root);
  const port = Number(match[1]);
  const index = await request(port, "/");
  assert.equal(index.status, 200);
  assert.match(index.headers["content-type"], /^text\/html/);
  assert.equal(index.text, "<h1>home</h1>");
  const listing = await request(port, "/sub");
  assert.equal(listing.status, 200);
  assert.match(listing.text, /href="\/sub\/hello%26.txt">hello&amp;\.txt/);
  assert.match(listing.text, /href="\/sub\/nested\/">nested\//);
  const file = await request(port, "/sub/hello%26.txt");
  assert.equal(file.text, "hello\n");
  assert.match(file.headers["content-type"], /^text\/plain/);
  const head = await request(port, "/sub/hello%26.txt", "HEAD");
  assert.equal(head.status, 200);
  assert.equal(head.headers["content-length"], "6");
  assert.equal(head.text, "");
  assert.equal((await request(port, "/absent")).status, 404);
  assert.equal((await request(port, "/%2e%2e/outside.txt")).status, 403);
  if (process.platform !== "win32") assert.equal((await request(port, "/outside.txt")).status, 403);
});

test("serve validates ports and the root", () => {
  assert.equal(runCli(["serve", "--port", "65536"]).status, 2);
  assert.equal(runCli(["serve", "--dir", BIN]).status, 2);
});
