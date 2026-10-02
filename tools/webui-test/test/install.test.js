'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const DIR = path.join(__dirname, '..');

test('install.sh は bash の構文として正しい', { skip: process.platform === 'win32' }, () => {
  execFileSync('bash', ['-n', path.join(DIR, 'install.sh')]);
});

test('install.ps1 は BOM 付き UTF-8（Windows PowerShell 5.1 が日本語を読み違えない）', () => {
  const head = fs.readFileSync(path.join(DIR, 'install.ps1')).subarray(0, 3);
  assert.deepStrictEqual([...head], [0xef, 0xbb, 0xbf]);
});

test('install.py は Windows では install.ps1 を PowerShell で、ほかでは install.sh を呼ぶ', () => {
  const py = process.platform === 'win32' ? 'python' : 'python3';
  const code = 'import json, sys; sys.path.insert(0, sys.argv[1]); import install; ' +
    'print(json.dumps([install.command(["--check", "--skip-browser"], True), install.command(["--with-deps"], False)]))';
  const [win, other] = JSON.parse(execFileSync(py, ['-c', code, DIR], { encoding: 'utf8' }));
  assert.deepStrictEqual(win.slice(1, 6), ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(DIR, 'install.ps1')]);
  assert.deepStrictEqual(win.slice(6), ['-Check', '-SkipBrowser']);
  assert.deepStrictEqual(other, ['bash', path.join(DIR, 'install.sh'), '--with-deps']);
});
