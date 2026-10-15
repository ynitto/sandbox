'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { cli, tmp } = require('./helpers');

const EXAMPLES = path.join(__dirname, '..', 'examples');

test('例: 仕様・実装と突き合わせて、主な規則がひととおり出る', async () => {
  const r = await cli([path.join(EXAMPLES, 'cases'), '--spec', path.join(EXAMPLES, 'spec'), '--source', path.join(EXAMPLES, 'app'), '-f', 'json', '-c', path.join(EXAMPLES, 'none.yaml')].filter((x) => !x.endsWith('none.yaml') && x !== '-c'));
  assert.equal(r.code, 1);
  const data = JSON.parse(r.out);
  const got = new Set(data.findings.map((f) => f.code));
  for (const code of ['C01', 'C04', 'D02', 'D06', 'D07', 'G03', 'I01', 'M01', 'O02', 'O03', 'O04', 'S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S08', 'U01', 'X01', 'X03']) {
    assert.ok(got.has(code), `${code} が出ない`);
  }
  assert.equal(data.summary.cases, 20);
});

test('終了コード: --fail-on と --min-severity', async () => {
  const dir = tmp({ 'c.yaml': '- {id: A, title: a, preconditions: [画面を開いている], steps: [押す], expected: [「エラー」が正しく出る]}\n' });
  const p = path.join(dir, 'c.yaml');
  assert.equal((await cli([p])).code, 0);
  assert.equal((await cli([p, '--fail-on', 'warning'])).code, 1);
  assert.doesNotMatch((await cli([p, '--min-severity', 'error'])).out, /D06/);
  assert.equal((await cli([p, '--fail-on', 'never', '--format', 'markdown'])).code, 0);
});

test('設定: 規則を止める・重さを変える・書式（JSON Schema）に合わせる', async () => {
  const dir = tmp({
    'cases/c.yaml': 'cases:\n  - {id: A, title: a, preconditions: [画面を開いている], steps: [押す], expected: [正しく出る], extra: 1}\n',
    'schema.json': JSON.stringify({ type: 'object', properties: { cases: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['id', 'title', 'steps', 'expected'], properties: { id: {}, title: {}, preconditions: {}, steps: {}, expected: {} } } } } }),
    'conf.yaml': 'cases: [cases]\nformat: { schema: schema.json }\nrules: { D06: off, G02: error }\n',
  });
  const r = await cli(['-c', path.join(dir, 'conf.yaml'), '-f', 'json']);
  const got = Object.fromEntries(JSON.parse(r.out).findings.map((f) => [f.code, f]));
  assert.ok(!got.D06);
  assert.equal(got.G02.severity, 'error');
  assert.match(got.D16.message, /extra/);
  assert.equal(got.D16.line, 2);
  assert.equal(r.code, 1);
  const bad = tmp({ 'conf.yaml': 'rules: { Z99: off }\n' });
  assert.equal((await cli(['-c', path.join(bad, 'conf.yaml'), path.join(dir, 'cases')])).code, 2);
});

test('結果ファイル（evidence）', async () => {
  const dir = tmp({ 'c.yaml': '- {id: A, title: a, steps: [押す], expected: [TBD]}\n', 'ok.yaml': '- {id: B, title: b, preconditions: [画面を開いている], steps: [押す], expected: [「エラー」と表示される]}\n' });
  const ev = path.join(dir, 'out', 'evidence.json');
  const cwd = process.cwd();
  process.chdir(dir);
  try {
    await cli(['c.yaml', 'ok.yaml', '--evidence', ev]);
  } finally { process.chdir(cwd); }
  const data = JSON.parse(fs.readFileSync(ev, 'utf8'));
  assert.equal(data.version, 1);
  assert.equal(data.root, '..');
  const byId = Object.fromEntries(data.items.map((x) => [x.id, x]));
  assert.equal(byId['testcase-audit/errors'].value, 1);
  assert.equal(byId['testcase-audit/c'].status, 'failed');
  assert.match(byId['testcase-audit/c'].error, /D07/);
  assert.equal(byId['testcase-audit/ok'].status, 'passed');
});

test('spec・rules・schema・使い方の誤り', async () => {
  const s = await cli(['spec', path.join(EXAMPLES, 'spec')]);
  assert.equal(s.code, 0);
  assert.match(s.out, /受付 →（取り消す）→ 取消済み/);
  assert.match((await cli(['rules'])).out, /S08/);
  assert.match((await cli(['schema'])).out, /conditions/);
  assert.equal((await cli([])).code, 2);
  assert.equal((await cli(['nothing-here.yaml'])).code, 2);
  assert.equal((await cli(['x.yaml', '--format', 'xml'])).code, 2);
});
