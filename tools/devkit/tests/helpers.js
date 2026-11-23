"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

const BIN = path.join(__dirname, "..", "bin", "devkit.js");

// devkit をサブプロセスで起動する。stdout/stderr は UTF-8 文字列。
// env は既定で process.env にマージされる。replaceEnv: true で env そのものを使う。
function runCli(args, { input = "", cwd, env, replaceEnv = false } = {}) {
  return spawnSync(process.execPath, [BIN, ...args], {
    input,
    cwd,
    env: env ? (replaceEnv ? env : { ...process.env, ...env }) : process.env,
    encoding: "utf8",
  });
}

// stdout/stderr を Buffer で受け取る版（文字コード系のテスト用）。
function runCliBuffer(args, { input = Buffer.alloc(0), cwd, env, replaceEnv = false } = {}) {
  return spawnSync(process.execPath, [BIN, ...args], {
    input,
    cwd,
    env: env ? (replaceEnv ? env : { ...process.env, ...env }) : process.env,
  });
}

module.exports = { runCli, runCliBuffer, BIN };
