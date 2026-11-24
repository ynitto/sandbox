"use strict";

// コマンド自動発見の検証: 全コマンドが一覧と --help に現れること、
// 雛形ファイルが登録されないこと。

const assert = require("node:assert/strict");
const test = require("node:test");
const { discoverCommands } = require("../core/cli");
const { runCli } = require("./helpers");

const EXPECTED = ["branches", "cron", "csv", "env", "ip", "jwt", "semver", "toc", "ts", "url"];

test("all commands are discovered", () => {
  const commands = discoverCommands();
  assert.deepEqual([...commands.keys()].sort(), EXPECTED);
});

test("top level help lists every command", () => {
  const result = runCli(["--help"]);
  assert.equal(result.status, 0);
  for (const name of EXPECTED) {
    assert.ok(result.stdout.includes(name), name);
  }
});

test("every command shows help", () => {
  for (const name of EXPECTED) {
    const result = runCli([name, "--help"]);
    assert.equal(result.status, 0, `${name}: ${result.stderr}`);
    assert.ok(result.stdout.includes(`devkit ${name}`), name);
  }
});

test("template file is not registered", () => {
  const result = runCli(["example"]);
  assert.equal(result.status, 2);
});

test("unknown command is an error", () => {
  const result = runCli(["no-such-command"]);
  assert.equal(result.status, 2);
  assert.ok(result.stderr.includes("invalid choice"));
});

test("no arguments is an error", () => {
  const result = runCli([]);
  assert.equal(result.status, 2);
});
