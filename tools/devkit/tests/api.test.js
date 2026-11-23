"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { listCommands, runCommand, run } = require("..");
const jwt = require("../commands/jwt");

const token = [
  Buffer.from('{"alg":"none"}').toString("base64url"),
  Buffer.from('{"sub":"利用者"}').toString("base64url"),
  "",
].join(".");

test("listCommands returns sorted command metadata", () => {
  const commands = listCommands();
  assert.deepEqual(commands.map(({ name }) => name), [
    "branches", "cron", "csv", "env", "ip", "jwt", "semver", "toc", "ts", "url",
  ]);
  for (const { name, help, usage } of commands) {
    assert.equal(typeof help, "string");
    assert.ok(help.length > 0, name);
    assert.ok(usage.startsWith(`devkit ${name}`), name);
  }
});

test("runCommand decodes a JWT argument", async () => {
  const { code, stdout, stderr, stdoutBuffer } = await runCommand("jwt", [token]);
  assert.equal(code, 0, stderr);
  assert.match(stdout, /Header:/);
  assert.match(stdout, /Payload:/);
  assert.deepEqual(stdoutBuffer, Buffer.from(stdout));
});

test("runCommand decodes a JWT from string or Buffer stdin", async () => {
  for (const stdin of [token, Buffer.from(token)]) {
    const { code, stdout, stderr } = await runCommand("jwt", [], { stdin });
    assert.equal(code, 0, stderr);
    assert.match(stdout, /利用者/);
  }
});

test("runCommand returns command help on stdout", async () => {
  const { code, stdout, stderr } = await runCommand("jwt", ["--help"]);
  assert.equal(code, 0);
  assert.match(stdout, /usage: devkit jwt/);
  assert.equal(stderr, "");
});

test("runCommand returns an error for an unknown command", async () => {
  const { code, stderr } = await runCommand("no-such-command");
  assert.equal(code, 2);
  assert.match(stderr, /invalid choice/);
});

test("runCommand converts epoch zero", async () => {
  const { code, stderr } = await runCommand("ts", ["0"]);
  assert.equal(code, 0, stderr);
});

test("runCommand resolves relative paths from options.cwd", async (t) => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-api-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(path.join(cwd, "before.env"), "A=1\n");
  fs.writeFileSync(path.join(cwd, "after.env"), "A=1\n");
  const { code, stderr } = await runCommand("env", ["before.env", "after.env"], { cwd });
  assert.equal(code, 0, stderr);
});

test("run accepts full argv and reports missing commands", async () => {
  assert.deepEqual(await run(["ts", "0"]), await runCommand("ts", ["0"]));
  const { code, stderr } = await run();
  assert.equal(code, 2);
  assert.match(stderr, /コマンドを指定してください/);
});

test("runCommand captures csv output written synchronously", async () => {
  const { code, stdout, stderr } = await runCommand("csv", ["-"], { stdin: "a,b\n1,2\n3,4\n" });
  assert.equal(code, 0, stderr);
  assert.ok(stdout.length > 0);
  assert.match(stdout, /a/);
});

test("runCommand preserves output written before an unexpected exception", async (t) => {
  // 実ファイルを追加せず、自動発見されるコマンドから例外を発生させる。
  t.mock.method(jwt, "run", async (_argv, io) => {
    io.out("before ");
    io.err("warning\n");
    const bytes = Buffer.from("日本語");
    io.outBuffer(bytes.subarray(0, 2));
    io.outBuffer(bytes.subarray(2));
    io.out(" after\n");
    throw new Error("unexpected failure");
  });
  assert.deepEqual(await runCommand("jwt"), {
    code: 1,
    stdout: "before 日本語 after\n",
    stderr: "warning\nunexpected failure\n",
    stdoutBuffer: Buffer.from("before 日本語 after\n"),
  });
});
