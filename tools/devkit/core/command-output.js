"use strict";

const { writeFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { UsageError } = require("./args");

function writeOutput(data, file, io) {
  if (file === undefined) {
    if (Buffer.isBuffer(data)) io.outBuffer(data);
    else io.out(data);
    return;
  }
  try {
    writeFileSync(resolve(io.cwd, file), data);
  } catch (error) {
    throw new UsageError(`出力を書き込めません: ${error.message}`);
  }
}

module.exports = { writeOutput };
