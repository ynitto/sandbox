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
    "b32", "b64", "branches", "case", "cert", "color", "cron", "csv", "diff", "dns", "encode", "env", "hash", "http",
    "img-convert", "img-exif", "img-info", "img-resize", "ip",
    "json", "jwt", "lines", "mime", "port", "pw", "qr", "regex", "semver", "serve",
    "table", "toc", "ts", "ulid", "url", "uuid", "yaml",
  ]);
  for (const { name, help, usage, example } of commands) {
    assert.equal(typeof help, "string");
    assert.ok(help.length > 0, name);
    assert.ok(usage.startsWith(`devkit ${name}`), name);
    assert.equal(typeof example, "string");
    assert.ok(example.startsWith(`devkit ${name} `), name);
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

test("runCommand rejects an invalid stdin type as a usage error", async () => {
  await assert.rejects(() => runCommand("jwt", [], { stdin: null }), TypeError);
  await assert.rejects(() => runCommand("jwt", [], { stdin: 42 }), /options\.stdin/);
  assert.throws(() => runCommand("jwt", "not-an-array"), TypeError);
  await assert.rejects(() => runCommand("jwt", [42]), TypeError);
});

test("runCommand passes a copy of env so commands cannot pollute the host", async (t) => {
  t.mock.method(jwt, "run", async (_argv, io) => {
    io.env.DEVKIT_API_TEST_MARKER = "polluted";
    return 0;
  });
  const env = { PATH: process.env.PATH };
  const { code } = await runCommand("jwt", [], { env });
  assert.equal(code, 0);
  assert.equal(env.DEVKIT_API_TEST_MARKER, undefined);
  assert.equal(process.env.DEVKIT_API_TEST_MARKER, undefined);
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

for (const [name, args, expected] of [
  ["uuid", ["--count", "1"], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\n$/],
  ["pw", ["--length", "12"], /^.{12}\n$/],
  ["b64", ["hello"], /^aGVsbG8=\n$/],
  ["hash", ["abc"], /^ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n$/],
  ["json", ["--path", "name", '{"name":"sample"}'], /^sample\n$/],
  ["regex", ["[0-9]+", "ID: 123"], /^match 1: 123/],
  ["color", ["hsl(0, 100%, 50%)"], /^hex: #ff0000\nrgb: rgb\(255, 0, 0\)/],
  ["ulid", ["00000000000000000000000000"], /^1970-01-01T00:00:00\.000Z\n$/],
  ["b32", ["foo"], /^MZXW6===\n$/],
  ["case", ["--to", "snake", "HTTPServer"], /^http_server\n$/],
  ["mime", ["--json", path.join(__dirname, "fixtures", "sample-rgba.png")], /"mime":"image\/png"/],
]) {
  test("runCommand executes " + name, async () => {
    const result = await runCommand(name, args);
    assert.equal(result.code, 0, result.stderr);
    assert.match(result.stdout, expected);
  });
}

test("runCommand round-trips binary Base32 output", async () => {
  const input = Buffer.from([0, 128, 255]);
  const encoded = await runCommand("b32", [], { stdin: input });
  assert.equal(encoded.code, 0, encoded.stderr);
  const decoded = await runCommand("b32", ["--decode"], { stdin: encoded.stdout });
  assert.equal(decoded.code, 0, decoded.stderr);
  assert.deepEqual(decoded.stdoutBuffer, input);
});

for (const [name, args, stdin, expected] of [
  ["lines", ["--uniq", "--count"], "b\na\nb\n", "2\tb\n1\ta\n"],
  ["table", [], "a,b\nc,d\n", "| a | b |\n| --- | --- |\n| c | d |\n"],
  ["yaml", [], "enabled: true\n", '{\n  "enabled": true\n}\n'],
  ["encode", ["--json"], "日本語\n", '{"file":"-","encoding":"utf8","bom":false,"lineEnding":"LF"}\n'],
]) {
  test("runCommand reads " + name + " input from stdin", async () => {
    const result = await runCommand(name, args, { stdin });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, expected);
  });
}

test("runCommand returns unified diff output with code 1", async (t) => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "devkit-api-diff-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  fs.writeFileSync(path.join(cwd, "before"), "old\n");
  const result = await runCommand("diff", ["before", "-"], { cwd, stdin: "new\n" });
  assert.equal(result.code, 1, result.stderr);
  assert.equal(result.stdout, "--- a/before\n+++ b/-\n@@ -1 +1 @@\n-old\n+new\n");
});
