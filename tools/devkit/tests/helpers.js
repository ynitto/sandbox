"use strict";

const { execFile, spawnSync } = require("child_process");
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

// 同じプロセスのローカルサーバーを動かしながら CLI を実行する。
function runCliAsync(args) {
  return new Promise((resolve, reject) => {
    execFile(process.execPath, [BIN, ...args], { timeout: 15000 }, (error, stdout, stderr) => {
      if (error && typeof error.code !== "number") reject(error);
      else resolve({ status: error ? error.code : 0, stdout, stderr });
    });
  });
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
}

module.exports = { runCli, runCliBuffer, runCliAsync, listen, close, BIN };
