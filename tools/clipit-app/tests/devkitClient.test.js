"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");
const { resolveDevkitDir, loadDevkit } = require("../src/devkitClient");

test("loads the repository devkit API from the app directory", async () => {
  const appDir = path.resolve(__dirname, "..");
  const options = { appDir, env: {} };
  assert.equal(resolveDevkitDir(options), path.resolve(appDir, "../devkit"));
  const { listCommands, runCommand } = loadDevkit(options);
  const commands = listCommands();
  assert.equal(commands.length, 36);
  const names = new Set(commands.map(({ name }) => name));
  for (const name of [
    "jwt", "img-info", "img-resize", "img-convert", "img-exif", "qr",
    "color", "ulid", "b32", "mime", "serve", "diff", "case", "lines", "table", "yaml", "encode",
  ]) {
    assert.ok(names.has(name), name);
  }
  for (const { name, example } of commands) {
    assert.equal(typeof example, "string", `${name} must have an example`);
    const invocation = `devkit ${name}`;
    assert.ok(example === invocation || example.startsWith(invocation + " "),
      `${name} example must start with "${invocation}"`);
  }
  const timestamp = await runCommand("ts", ["0"]);
  assert.equal(timestamp.code, 0);
  assert.match(timestamp.stdout, /1970/);
  const uuid = await runCommand("uuid", []);
  assert.equal(uuid.code, 0);
  assert.match(uuid.stdout.trim(), /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i);
  const color = await runCommand("color", ["#ff0000"]);
  assert.equal(color.code, 0, color.stderr);
  assert.match(color.stdout, /255/);
  const image = path.resolve(appDir, "../devkit/tests/fixtures/sample-rgba.png");
  const info = await runCommand("img-info", [image]);
  assert.equal(info.code, 0, info.stderr);
  assert.match(info.stdout, /PNG/);
  assert.equal((await runCommand("unknown-clipit-test-command", [])).code, 2);
});

test("resolves overrides and packaged devkit before repository paths", () => {
  const appDir = path.resolve("app");
  const resourcesPath = path.resolve("resources");
  const options = { appDir, resourcesPath, exists: () => true };
  assert.equal(resolveDevkitDir({ ...options, env: { CLIPIT_DEVKIT_PATH: "custom" } }), "custom");
  assert.equal(resolveDevkitDir({ ...options, env: {} }), path.join(resourcesPath, "devkit"));
  assert.equal(resolveDevkitDir({ ...options, env: {}, resourcesPath: undefined }),
    path.resolve(appDir, "../../devkit"));
  assert.throws(() => loadDevkit({ ...options, env: {}, exists: () => false }), /index.js が見つかりません/);
});
