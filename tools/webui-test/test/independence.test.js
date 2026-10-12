'use strict';
// webui-test は、結果を読む側の道具（文書の整合を取る道具など）を知らずに動く。
// 名前を出す・設定や作業ファイルを読む処理が混ざると、その道具の無いプロジェクトで使えない前提ができる。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const FORBIDDEN = [/codd/i, /\.statemachine/, /\.plans\b/];

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === 'node_modules' ? [] : files(p);
    return /\.(js|md|json|ya?ml|sh|ps1|py)$/.test(e.name) ? [p] : [];
  });
}

test('独立: 本体とインストーラは、結果を読む側の道具の名前・設定・作業ファイルに触れない', () => {
  const targets = [...['src', 'bin', 'scripts'].flatMap((d) => files(path.join(ROOT, d))),
    ...['install.py', 'install.sh', 'install.ps1', 'package.json'].map((f) => path.join(ROOT, f))];
  const hits = [];
  for (const file of targets) {
    fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      if (FORBIDDEN.some((re) => re.test(line))) hits.push(`${path.relative(ROOT, file)}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});
