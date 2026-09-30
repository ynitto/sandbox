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
