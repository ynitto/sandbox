#!/usr/bin/env node
"use strict";

const fs = require("fs");
const { main } = require("../core/cli");

const io = {
  out(text) {
    process.stdout.write(text);
  },
  err(text) {
    process.stderr.write(text);
  },
  outBuffer(buffer) {
    process.stdout.write(buffer);
  },
  outSync(buffer) {
    // 同期書き込み。EPIPE を呼び出し側が検知して独自の終了コードで報告できる。
    for (let written = 0; written < buffer.length;) {
      written += fs.writeSync(1, buffer, written, buffer.length - written);
    }
  },
  readStdin() {
    return fs.readFileSync(0);
  },
  stdinIsTTY: Boolean(process.stdin.isTTY),
  env: process.env,
  cwd: process.cwd(),
};

// パイプが閉じられた場合にスタックトレースを出さない。
process.stdout.on("error", (error) => {
  if (error.code === "EPIPE") process.exit(1);
  throw error;
});

main(process.argv.slice(2), io).then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(error);
    process.exitCode = 1;
  }
);
