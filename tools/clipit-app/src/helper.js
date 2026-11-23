"use strict";

const { spawn } = require("node:child_process");
const { existsSync } = require("node:fs");
const path = require("node:path");

function resolveHelperCommand({ env = process.env, platform = process.platform, resourcesPath, appDir, exists = existsSync }) {
  const python = env.CLIPIT_HELPER_PYTHON || (platform === "win32" ? "python" : "python3");
  const override = env.CLIPIT_HELPER_PATH;
  if (override) {
    return path.extname(override).toLowerCase() === ".py"
      ? { command: python, args: [override] }
      : { command: override, args: [] };
  }
  if (resourcesPath) {
    const executable = path.join(resourcesPath, "helper", "clipit-helper.exe");
    if (exists(executable)) return { command: executable, args: [] };
  }
  if (appDir) {
    const script = path.join(appDir, "helper", "clipit_helper.py");
    if (exists(script)) return { command: python, args: [script] };
  }
  return null;
}

function createHelperClient(resolved, { spawnImpl = spawn, timeoutMs = 15000 } = {}) {
  function run(subcommand, request, { timeoutMs: callTimeoutMs } = {}) {
    return new Promise((resolve, reject) => {
      if (!resolved) throw new Error("ClipIt ヘルパーが見つかりません。");
      const limit = callTimeoutMs ?? timeoutMs;
      const input = JSON.stringify(request);
      let child;
      try {
        child = spawnImpl(resolved.command, [...resolved.args, subcommand], {
          windowsHide: true,
          stdio: ["pipe", "pipe", "pipe"],
        });
      } catch (error) {
        throw new Error(`ヘルパーを起動できませんでした。 ${error.message}`, { cause: error });
      }

      let stdout = "";
      let stderr = "";
      let inputError;
      let settled = false;
      let closed = false;
      let escalation;
      const settle = (action, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        action(value);
      };
      const timer = setTimeout(() => {
        settle(reject, new Error(`ヘルパーがタイムアウトしました (${limit} ms)。`));
        child.kill();
        // kill が効かないプロセスは少し待ってから強制終了する。
        escalation = setTimeout(() => {
          if (!closed) child.kill("SIGKILL");
        }, 1000);
        escalation.unref?.();
      }, limit);

      child.stdout.setEncoding("utf8").on("data", (chunk) => { stdout += chunk; });
      child.stderr.setEncoding("utf8").on("data", (chunk) => { stderr += chunk; });
      child.stdin.on("error", (error) => { inputError = error; });
      child.once("error", (error) => {
        settle(reject, new Error(`ヘルパーを起動できませんでした。 ${error.message}`, { cause: error }));
      });
      child.once("close", (code, signal) => {
        closed = true;
        clearTimeout(escalation);
        let response;
        try {
          response = JSON.parse(stdout);
        } catch (error) {
          const detail = stderr.trim() || inputError?.message || signal || `exit code ${code}`;
          settle(reject, new Error(`ヘルパーの出力が JSON ではありません。 ${detail}`, { cause: error }));
          return;
        }
        if (response?.ok === false) {
          settle(reject, new Error(`ヘルパーの処理に失敗しました。 ${response.error ?? ""}`));
        } else if (response?.ok !== true || !Object.hasOwn(response, "result")) {
          settle(reject, new Error("ヘルパーの応答形式が不正です。"));
        } else if (code !== 0) {
          settle(reject, new Error(`ヘルパーが異常終了しました。 ${stderr.trim() || signal || code}`));
        } else {
          settle(resolve, response.result);
        }
      });
      child.stdin.end(input, "utf8");
    });
  }

  return { run };
}

module.exports = { resolveHelperCommand, createHelperClient };
