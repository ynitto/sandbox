"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli, runCliBuffer } = require("./helpers");

const text = "日本語のテスト\nこんにちは";
const samples = [
  ["utf8", Buffer.from(text)],
  ["sjis", Buffer.from("93fa967b8cea82cc8365835883670a82b182f182c982bf82cd", "hex")],
  ["euc-jp", Buffer.from("c6fccbdcb8eca4cea5c6a5b9a5c80aa4b3a4f3a4cba4c1a4cf", "hex")],
];

test("encode detects UTF-8, Shift_JIS, and EUC-JP and converts losslessly", () => {
  for (const [encoding, input] of samples) {
    const check = runCli(["encode", "--json"], { input });
    assert.equal(check.status, 0, check.stderr);
    assert.deepEqual(JSON.parse(check.stdout), { file: "-", encoding, bom: false, lineEnding: "LF" });
    const encoded = runCliBuffer(["encode", "--to", encoding, "-"], { input: Buffer.from(text) });
    assert.equal(encoded.status, 0, encoded.stderr.toString());
    assert.deepEqual(encoded.stdout, input);
    const decoded = runCliBuffer(["encode", "--to", "utf8"], { input: encoded.stdout });
    assert.equal(decoded.status, 0, decoded.stderr.toString());
    assert.equal(decoded.stdout.toString(), text);
  }
});

test("encode detects UTF-16 BOM and converts newlines and BOM independently", () => {
  const little = Buffer.from(text, "utf16le");
  for (const [encoding, bytes] of [
    ["utf-16le", Buffer.concat([Buffer.from("fffe", "hex"), little])],
    ["utf-16be", Buffer.concat([Buffer.from("feff", "hex"), Buffer.from(little).swap16()])],
  ]) {
    const check = runCli(["encode", "--check", "--json"], { input: bytes });
    assert.equal(check.status, 0, check.stderr);
    assert.deepEqual(JSON.parse(check.stdout), { file: "-", encoding, bom: true, lineEnding: "LF" });
    const utf8 = runCliBuffer(["encode", "--to", "utf8", "--crlf", "--no-bom"], { input: bytes });
    assert.equal(utf8.status, 0, utf8.stderr.toString());
    assert.deepEqual(utf8.stdout, Buffer.from(text.replace("\n", "\r\n")));
  }
  const utf16 = runCliBuffer(["encode", "--to", "utf-16be", "--bom"], { input: Buffer.from("A") });
  assert.deepEqual(utf16.stdout, Buffer.from("feff0041", "hex"));
  assert.equal(JSON.parse(runCli(["encode", "--json"], { input: "a\r\nb\nc\r" }).stdout).lineEnding, "混在");
});

test("encode reports unrepresentable characters without writing partial output", () => {
  for (const target of ["ascii", "sjis", "euc-jp"]) {
    const result = runCli(["encode", "--to", target], { input: "ascii🙂" });
    assert.equal(result.status, 2, result.stderr);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /表現できない文字.*🙂/);
  }
  assert.equal(runCli(["encode", "--lf", "--crlf"]).status, 2);
  assert.equal(runCli(["encode", "--in-place"]).status, 2);
});

test("encode writes output and only creates an in-place backup when requested", (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-encode-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const file = join(cwd, "input.txt");
  const backup = file + ".bak";
  fs.writeFileSync(file, "a\nb\n");
  assert.equal(runCli(["encode", "--in-place", "--crlf", "input.txt"], { cwd }).status, 0);
  assert.equal(fs.readFileSync(file, "utf8"), "a\r\nb\r\n");
  assert.equal(fs.existsSync(backup), false);
  assert.equal(runCli(["encode", "--in-place", "--backup", "--lf", "input.txt"], { cwd }).status, 0);
  assert.equal(fs.readFileSync(backup, "utf8"), "a\r\nb\r\n");
  assert.equal(fs.readFileSync(file, "utf8"), "a\nb\n");
  assert.equal(runCli(["encode", "--out", "output.txt", "--cr", "input.txt"], { cwd }).status, 0);
  assert.equal(fs.readFileSync(join(cwd, "output.txt"), "utf8"), "a\rb\r");
  if (process.platform !== "win32") {
    const alias = join(cwd, "alias.txt");
    fs.symlinkSync("input.txt", alias);
    const result = runCli(["encode", "--in-place", "--backup", "--crlf", "alias.txt"], { cwd });
    assert.equal(result.status, 0, result.stderr);
    assert.ok(fs.lstatSync(alias).isSymbolicLink());
    assert.equal(fs.readFileSync(alias, "utf8"), "a\r\nb\r\n");
    assert.equal(fs.readFileSync(alias + ".bak", "utf8"), "a\nb\n");
  }
});

test("encode detects BOM-less UTF-16 from the NUL byte pattern", () => {
  const le = Buffer.from("abc test 123\n", "utf16le");
  const checkLe = runCli(["encode", "--json"], { input: le });
  assert.equal(checkLe.status, 0, checkLe.stderr);
  assert.equal(JSON.parse(checkLe.stdout).encoding, "utf-16le");
  const be = Buffer.from("abc test 123\n", "utf16le").swap16();
  const checkBe = runCli(["encode", "--json"], { input: be });
  assert.equal(checkBe.status, 0, checkBe.stderr);
  assert.equal(JSON.parse(checkBe.stdout).encoding, "utf-16be");
});

test("encode rejects --check combined with conversion options", () => {
  assert.equal(runCli(["encode", "--check", "--to", "sjis"], { input: Buffer.from(text) }).status, 2);
  assert.equal(runCli(["encode", "--check", "--crlf"], { input: Buffer.from(text) }).status, 2);
});
