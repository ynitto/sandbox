'use strict';
// glob の展開（Node の版にもシェルにも頼らない）とパッケージの中身
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { spawnSync } = require('child_process');
const { globSync, toRegex } = require('../src/glob');
const { tmp, cli } = require('./helpers');

test('glob を正規表現にする', () => {
  const m = (g, p) => new RegExp(toRegex(g)).test(p);
  assert.ok(m('**/*.yaml', 'a.yaml'));
  assert.ok(m('**/*.yaml', 'x/y/a.yaml'));
  assert.ok(!m('*.yaml', 'x/a.yaml'));
  assert.ok(m('cases/{login,orders}.y?ml', 'cases/orders.yaml'));
  assert.ok(!m('cases/{login,orders}.y?ml', 'cases/other.yaml'));
  assert.ok(m('t[0-9].json', 't3.json'));
  assert.ok(!m('t[!0-9].json', 't3.json'));
});

test('glob を展開する（node_modules・隠しフォルダはたどらない。\\ の区切りも受ける）', () => {
  const dir = tmp({ 'qa/a/x.yaml': 'a', 'qa/b/y.yml': 'b', 'qa/c.json': 'c', 'qa/node_modules/z.yaml': 'z', 'qa/.cache/w.yaml': 'w' });
  const rel = (ps) => ps.map((p) => path.relative(dir, p).split(path.sep).join('/'));
  assert.deepEqual(rel(globSync('qa/**/*.{yaml,yml}', { cwd: dir })), ['qa/a/x.yaml', 'qa/b/y.yml']);
  assert.deepEqual(rel(globSync('qa\\*.json', { cwd: dir })), ['qa/c.json']);
  assert.deepEqual(rel(globSync(path.join(dir, 'qa', '*', '*.yaml'))), ['qa/a/x.yaml']);
});

test('CLI: 設定ファイル無しで、ファイル・glob をその場で渡せる', async () => {
  const dir = tmp({
    'qa/login/a.yaml': '- {id: A, title: a, preconditions: [画面を開いている], steps: [押す], expected: [TBD]}\n',
    'qa/order/b.yaml': '- {id: B, title: b, preconditions: [画面を開いている], steps: [押す], expected: [「出る」と表示される]}\n',
    'docs/req.md': '- REQ-1 出る\n',
  });
  const cwd = process.cwd();
  process.chdir(dir);
  try {
    let r = await cli(['qa/**/*.yaml', '--spec', 'docs/*.md', '-f', 'json']);
    const data = JSON.parse(r.out);
    assert.equal(data.summary.files, 2);
    assert.equal(data.summary.spec.files, 1);
    r = await cli(['qa/login/a.yaml']);
    assert.match(r.out, /^ケース: qa\/login\/a\.yaml\n/);
    assert.equal(r.code, 1);
    r = await cli(['qa/nothing/*.yaml']);
    assert.equal(r.code, 2);
    assert.match(r.err, /当てはまるファイルがありません/);
  } finally { process.chdir(cwd); }
});

test('パッケージ: グローバルに入れる中身に、本体と書式がそろっている', () => {
  const r = spawnSync('npm', ['pack', '--dry-run', '--json'], { cwd: path.join(__dirname, '..'), encoding: 'utf8', shell: process.platform === 'win32' });
  assert.equal(r.status, 0, r.stderr);
  const files = JSON.parse(r.stdout)[0].files.map((f) => f.path);
  for (const f of ['bin/testcase-lint.js', 'src/cli.js', 'src/glob.js', 'schema/testcase.schema.json', 'package.json']) assert.ok(files.includes(f), f);
  assert.ok(!files.some((f) => f.startsWith('test/') || f.startsWith('examples/')));
});
