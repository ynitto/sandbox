#!/usr/bin/env node
'use strict';

// Electron を起動せずに、dashboard と同じ資源制御モジュールを 1 回実行する CLI 入口
// （`npm run resources`）。本体は src/features/orchestration/main/resource-control.js。
// scripts/ は配布物（build.files）に入らないので、ここには CLI の引数処理以外を置かない。
const resourceControl = require('../src/features/orchestration/main/resource-control');

if (require.main === module) {
  try {
    process.stdout.write(`${JSON.stringify(resourceControl.run(resourceControl.parseArgs(process.argv.slice(2))))}\n`);
  } catch (err) {
    process.stderr.write(`agent-resource-control: ${err.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = resourceControl;
