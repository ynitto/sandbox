"use strict";

const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { ArgTypeError, UsageError } = require("./args");

function integer(min, max = Number.MAX_SAFE_INTEGER) {
  return (raw) => {
    const value = Number(raw);
    if (typeof raw !== "string" || !/^[+-]?\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) {
      const range = max === Number.MAX_SAFE_INTEGER ? `${min} 以上` : `${min}〜${max}`;
      throw new ArgTypeError(`${range} の整数を指定してください: ${raw}`);
    }
    return value;
  };
}

function readInput(text, file, io) {
  if (text !== undefined) return Buffer.from(text, "utf8");
  try {
    return file === undefined ? io.readStdin() : readFileSync(resolve(io.cwd, file));
  } catch (error) {
    throw new UsageError(`入力を読み込めません: ${error.message}`);
  }
}

module.exports = { integer, readInput };
