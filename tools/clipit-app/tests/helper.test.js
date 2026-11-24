"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { createHelperClient, resolveHelperCommand } = require("../src/helper");

test("helper resolution prefers overrides, packaged executable, then local script", () => {
  const resourcesPath = path.resolve("resources");
  const appDir = path.resolve("app");
  const executable = path.join(resourcesPath, "helper", "clipit-helper.exe");
  const script = path.join(appDir, "helper", "clipit_helper.py");
  const options = { env: {}, resourcesPath, appDir, platform: "win32", exists: () => true };

  assert.deepEqual(resolveHelperCommand({ ...options, env: { CLIPIT_HELPER_PATH: "custom.exe" } }),
    { command: "custom.exe", args: [] });
  assert.deepEqual(resolveHelperCommand({
    ...options,
    env: { CLIPIT_HELPER_PATH: "custom.py", CLIPIT_HELPER_PYTHON: "custom-python" },
  }), { command: "custom-python", args: ["custom.py"] });
  assert.deepEqual(resolveHelperCommand(options), { command: executable, args: [] });
  const local = { ...options, exists: (candidate) => candidate === script };
  assert.deepEqual(resolveHelperCommand(local), { command: "python", args: [script] });
  assert.deepEqual(resolveHelperCommand({ ...local, platform: "linux" }), { command: "python3", args: [script] });
  assert.equal(resolveHelperCommand({ ...options, exists: () => false }), null);
});

test("helper exchanges UTF-8 JSON and reports protocol failures", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clipit-helper-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const script = path.join(dir, "fake.cjs");
  fs.writeFileSync(script, `
    const request = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
    switch (process.argv[2]) {
      case "success":
        process.stdout.write(JSON.stringify({ ok: true, result: request }));
        break;
      case "error":
        process.stdout.write(JSON.stringify({ ok: false, error: "処理エラー" }));
        process.exitCode = 1;
        break;
      case "invalid":
        process.stdout.write("invalid output");
        break;
      case "wait":
        setInterval(() => {}, 1000);
        break;
    }
  `, "utf8");

  const resolved = { command: process.execPath, args: [script] };
  const client = createHelperClient(resolved);
  assert.deepEqual(await client.run("success", { text: "日本語" }), { text: "日本語" });
  await assert.rejects(client.run("error", {}), /処理エラー/);
  await assert.rejects(client.run("invalid", {}), /JSON/);
  await assert.rejects(createHelperClient(resolved, { timeoutMs: 100 }).run("wait", {}), /タイムアウト/);
  await assert.rejects(client.run("wait", {}, { timeoutMs: 100 }), /タイムアウト/);
  await assert.rejects(
    createHelperClient({ command: path.join(dir, "missing"), args: [] }).run("success", {}),
    /起動できませんでした/,
  );
});
