'use strict';
// testcase-lint は、結果を読む側の道具（文書の整合を取る道具など）も、ケースを作って動かす道具も知らずに動く。
// 名前を出す・設定や作業ファイルを読む処理が混ざると、その道具の無いプロジェクトで使えない前提ができる。
// 結果は version 1 の items 形式のファイルで渡し、ケースは JSON / YAML の一般的な形として読む。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const FORBIDDEN = [/codd/i, /\.statemachine/, /\.plans\b/, /webui-test/i, /\.codd\b/];

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === 'node_modules' ? [] : files(p);
    return /\.(js|json|ya?ml)$/.test(e.name) ? [p] : [];
  });
}

test('独立: 本体は、ほかの道具の名前・設定・作業ファイルに触れない', () => {
  const targets = [...['src', 'bin', 'schema'].flatMap((d) => files(path.join(ROOT, d))), path.join(ROOT, 'package.json')];
  const hits = [];
  for (const file of targets) {
    fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      if (FORBIDDEN.some((re) => re.test(line))) hits.push(`${path.relative(ROOT, file)}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});

test('独立: 依存はこのフォルダの package.json に書いたものだけ', () => {
  const pkg = require('../package.json');
  const allowed = new Set([...Object.keys(pkg.dependencies), 'fs', 'path', 'os', 'util', 'child_process']);
  const bad = [];
  for (const file of files(path.join(ROOT, 'src'))) {
    for (const m of fs.readFileSync(file, 'utf8').matchAll(/require\('([^']+)'\)/g)) {
      const name = m[1];
      if (name.startsWith('.') || name.startsWith('node:')) continue;
      const packageName = name.startsWith('@') ? name.split('/').slice(0, 2).join('/') : name.split('/')[0];
      if (!allowed.has(packageName)) bad.push(`${path.relative(ROOT, file)}: ${name}`);
    }
  }
  assert.deepEqual(bad, []);
});
