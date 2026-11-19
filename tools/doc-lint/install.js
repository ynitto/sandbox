#!/usr/bin/env node
'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseArgs } = require('node:util');

function run(command, args, cwd) {
  const windows = process.platform === 'win32';
  const result = spawnSync(command, windows ? args.map((arg) => `"${arg}"`) : args, {
    cwd, shell: windows, stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} に失敗しました (${result.status ?? result.signal})`);
  }
}

function main() {
  const { values } = parseArgs({
    options: { check: { type: 'boolean' }, help: { type: 'boolean', short: 'h' } },
  });
  if (values.help) {
    console.log('使い方: node install.js [--check]');
    return;
  }
  if (Number(process.versions.node.split('.')[0]) < 20) throw new Error('Node.js 20 以上が必要です');

  const packDir = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-lint-pack-'));
  try {
    console.log('doc-lint を固めます（npm pack）');
    run('npm', ['pack', '--silent', '--pack-destination', packDir], __dirname);
    const file = fs.readdirSync(packDir).find((name) => name.endsWith('.tgz'));
    if (!file) throw new Error('npm pack の出力が見つかりません');
    console.log('doc-lint をグローバルに入れます（npm install -g）');
    run('npm', ['install', '-g', path.join(packDir, file), '--omit=dev'], __dirname);
  } finally {
    fs.rmSync(packDir, { recursive: true, force: true });
  }

  run('doc-lint', ['--version'], __dirname);
  if (values.check) {
    const sample = path.join(__dirname, 'examples', 'sample');
    run('doc-lint', ['doctor'], sample);
    run('doc-lint', ['check', '--fail-on', 'never'], sample);
  }
}

try {
  main();
} catch (error) {
  console.error(`[失敗] ${error.message}`);
  process.exitCode = 2;
}
