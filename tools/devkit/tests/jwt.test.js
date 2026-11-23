"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createHmac } = require("node:crypto");
const { mkdtempSync, writeFileSync, rmSync } = require("node:fs");
const { join } = require("node:path");
const { main } = require("../core/cli");
const { runCli } = require("./helpers");

const segment = (text) => Buffer.from(text).toString("base64url");
function makeToken(payload, { alg = "HS256", secret } = {}) {
  const message = segment(JSON.stringify({ typ: "JWT", alg })) + "." + segment(payload);
  const signature = secret === undefined ? "" : createHmac("sha256", secret).update(message).digest("base64url");
  return message + "." + signature;
}
async function runMain(args, nowSeconds = 100) {
  let stdout = "";
  let stderr = "";
  const status = await main(["jwt", ...args], {
    cwd: process.cwd(), env: process.env, nowSeconds,
    out(text) { stdout += text; },
    err(text) { stderr += text; },
  });
  return { status, stdout, stderr };
}

test("decode", async () => {
  const token = makeToken('{"sub":"利用者","aud":"example"}', { secret: "test" });
  const { status, stdout, stderr } = await runMain([token]);
  assert.equal(status, 0);
  assert.equal(stderr, "");
  assert.ok(stdout.includes('Header:\n{\n  "typ": "JWT",\n  "alg": "HS256"\n}'));
  assert.ok(stdout.includes('Payload:\n{\n  "sub": "利用者",\n  "aud": "example"\n}'));
  assert.ok(stdout.includes("署名: あり (32 バイト)"));
  assert.ok(!stdout.includes("署名検証:"));
});

test("time claims", async () => {
  const token = makeToken('{"exp":100,"nbf":101,"iat":0,"auth_time":1}');
  const { status, stdout, stderr } = await runMain([token, "--tz", "Asia/Tokyo"]);
  assert.equal(status, 0);
  assert.equal(stderr, "");
  assert.ok(stdout.includes("exp: 1970-01-01T09:01:40+09:00"));
  assert.ok(stdout.includes("nbf: 1970-01-01T09:01:41+09:00"));
  assert.ok(stdout.includes("iat: 1970-01-01T09:00:00+09:00"));
  assert.ok(stdout.includes("auth_time: 1970-01-01T09:00:01+09:00"));
  assert.ok(stdout.includes("期限切れ"));
  assert.ok(stdout.includes("まだ有効になっていない"));
  const { stdout: utc } = await runMain([token], 99);
  assert.ok(utc.includes("exp: 1970-01-01T00:01:40+00:00"));
  assert.ok(!utc.includes("期限切れ"));
});

test("string time claims", async () => {
  const token = makeToken('{"exp":"99","nbf":"101","iat":"0","auth_time":"1"}');
  const { status, stdout, stderr } = await runMain([token]);
  assert.equal(status, 0);
  assert.equal(stderr, "");
  assert.ok(stdout.includes("exp: 1970-01-01T00:01:39+00:00"));
  assert.ok(stdout.includes("nbf: 1970-01-01T00:01:41+00:00"));
  assert.ok(stdout.includes("iat: 1970-01-01T00:00:00+00:00"));
  assert.ok(stdout.includes("auth_time: 1970-01-01T00:00:01+00:00"));
  assert.ok(stdout.includes("期限切れ"));
  assert.ok(stdout.includes("まだ有効になっていない"));
});

test("unusable time claims", async () => {
  const token = makeToken('{"exp":-Infinity,"nbf":Infinity,"iat":NaN,"auth_time":"unknown"}');
  const { status, stdout, stderr } = await runMain([token]);
  assert.equal(status, 0);
  assert.equal(stderr, "");
  assert.ok(stdout.includes("exp: 有限の UNIX 時刻ではありません (-inf)"));
  assert.ok(stdout.includes("nbf: 有限の UNIX 時刻ではありません (inf)"));
  assert.ok(stdout.includes("iat: 有限の UNIX 時刻ではありません (nan)"));
  assert.ok(stdout.includes("auth_time: 数値の UNIX 時刻ではありません"));
  assert.ok(!stdout.includes("警告:"));
});

test("JSON formatting preserves key order, large integers and floats", async () => {
  const token = makeToken('{"z":[1.0,1e-7,-0.0],"1":9007199254740993,"a":{"__proto__":"value"}}');
  const { status, stdout, stderr } = await runMain([token]);
  assert.equal(status, 0, stderr);
  assert.ok(stdout.includes(
    'Payload:\n{\n  "z": [\n    1.0,\n    1e-07,\n    -0.0\n  ],\n' +
    '  "1": 9007199254740993,\n  "a": {\n    "__proto__": "value"\n  }\n}'
  ));
});

test("nonfinite values are only displayed for top-level time claims", async () => {
  const { status, stdout, stderr } = await runMain([makeToken('{"exp":{"nested":NaN}}')]);
  assert.equal(status, 2);
  assert.equal(stdout, "");
  assert.match(stderr, /^不正な JWT \(Payload\): Out of range float values/);
});

for (const [token, reason] of [
  ["one.two", "パート数"], ["@.e30.", "base64url"], ["e30.e30.++", "base64url"],
  ["e30.e30.YWJj=", "パディング"], [segment("not JSON") + ".e30.", "Header"], ["e30.W10.", "オブジェクト"],
]) {
  test("invalid token " + token, async () => {
    const { status, stdout, stderr } = await runMain([token]);
    assert.equal(status, 2);
    assert.equal(stdout, "");
    assert.ok(stderr.includes(reason), stderr);
  });
}

test("HS256 verification", async () => {
  const token = makeToken('{"sub":"user"}', { secret: "秘密" });
  const { status, stdout } = await runMain(["--secret", "秘密", token]);
  assert.equal(status, 0);
  assert.ok(stdout.includes("署名検証: OK"));
});

test("wrong secret", async () => {
  const token = makeToken('{"sub":"user"}', { secret: "秘密" });
  const { status, stdout } = await runMain(["--secret", "wrong", token]);
  assert.equal(status, 3);
  assert.ok(stdout.includes("署名検証: NG"));
});

test("empty secret", async () => {
  const { status, stdout, stderr } = await runMain(["--secret", "", makeToken("{}", { secret: "" })]);
  assert.equal(status, 1);
  assert.equal(stdout, "");
  assert.ok(stderr.includes("--secret に空文字列は指定できません"));
});

test("unsupported algorithm", async () => {
  const { status, stdout } = await runMain(["--secret", "test", makeToken("{}", { alg: "none" })]);
  assert.equal(status, 3);
  assert.ok(stdout.includes("検証できません"));
});

test("stdin", () => {
  const token = makeToken('{"sub":"stdin"}');
  const result = runCli(["jwt"], { input: " \n" + token.replaceAll(".", ".\n\t") + " \n" });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.includes("Header:"));
  assert.ok(result.stdout.includes('Payload:\n{\n  "sub": "stdin"'));
  assert.ok(result.stdout.includes("署名: なし (0 バイト)"));
});

function temporaryDirectory(t) {
  const directory = mkdtempSync(join(__dirname, ".jwt-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("file and input priority", async (t) => {
  const path = join(temporaryDirectory(t), "token.txt");
  writeFileSync(path, makeToken('{"sub":"file"}') + "\n");
  const file = await runMain(["--file", path]);
  assert.equal(file.status, 0);
  assert.ok(file.stdout.includes('"sub": "file"'));
  const argument = await runMain(["--file", path, makeToken('{"sub":"argument"}')]);
  assert.equal(argument.status, 0);
  assert.ok(argument.stdout.includes('"sub": "argument"'));
});

test("missing file", async (t) => {
  const { status, stdout, stderr } = await runMain(["--file", join(temporaryDirectory(t), "missing.txt")]);
  assert.equal(status, 1);
  assert.equal(stdout, "");
  assert.ok(stderr.includes("エラー"));
});
