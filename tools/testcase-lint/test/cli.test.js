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
  for (const code of ['A01', 'A02', 'C01', 'C04', 'D02', 'D07', 'G03', 'I01', 'M01', 'O02', 'O03', 'O04', 'S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S08', 'U01', 'X01', 'X03']) {
    assert.ok(got.has(code), `${code} が出ない`);
  }
  assert.equal(data.summary.cases, 20);
});

test('終了コード: --fail-on と --min-severity', async () => {
  const dir = tmp({ 'c.yaml': '- {id: A, title: a, preconditions: [画面を開いている], steps: [押す], expected: [「エラー」が正しく出る]}\n' });
  const p = path.join(dir, 'c.yaml');
  assert.equal((await cli([p])).code, 0);
  assert.equal((await cli([p, '--fail-on', 'warning'])).code, 1);
  assert.doesNotMatch((await cli([p, '--min-severity', 'error'])).out, /A01/);
  assert.equal((await cli([p, '--fail-on', 'never', '--format', 'markdown'])).code, 0);
});

test('設定: 規則を止める・重さを変える・書式（JSON Schema）に合わせる', async () => {
  const dir = tmp({
    'cases/c.yaml': 'cases:\n  - {id: A, title: a, preconditions: [画面を開いている], steps: [押す], expected: [正しく出る], extra: 1}\n',
    'schema.json': JSON.stringify({ type: 'object', properties: { cases: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['id', 'title', 'steps', 'expected'], properties: { id: {}, title: {}, preconditions: {}, steps: {}, expected: {} } } } } }),
    'conf.yaml': 'cases: [cases]\nformat: { schema: schema.json }\nrules: { A01: off, G02: error }\n',
  });
  const r = await cli(['-c', path.join(dir, 'conf.yaml'), '-f', 'json']);
  const got = Object.fromEntries(JSON.parse(r.out).findings.map((f) => [f.code, f]));
  assert.ok(!got.A01);
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
  assert.equal(byId['testcase-lint/errors'].value, 1);
  assert.equal(byId['testcase-lint/c'].status, 'failed');
  assert.match(byId['testcase-lint/c'].error, /D07/);
  assert.equal(byId['testcase-lint/ok'].status, 'passed');
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

// 今のフォルダを変えて CLI を動かす
async function inDir(dir, args) {
  const cwd = process.cwd();
  process.chdir(dir);
  try { return await cli(args); } finally { process.chdir(cwd); }
}

test('オプション無し: 設定ファイルが無ければ、よくあるフォルダを探して使う', async () => {
  const dir = tmp({
    'tests/cases/a.yaml': '- {id: A, title: a, requirement: REQ-1, preconditions: [画面を開いている], steps: [押す], expected: [「出る」と表示される]}\n',
    'docs/specs/req.md': '- REQ-1 出る\n- REQ-2 消える\n',
    'src/msg.json': '{"a": "出る"}\n',
  });
  const r = await inDir(dir, []);
  assert.match(r.out, /^ケース: tests\/cases \/ 仕様: docs\/specs \/ 実装: src（設定ファイルが無いので/);
  assert.match(r.out, /REQ-2/);
  assert.equal(r.code, 1);
});

test('オプション無し: 設定ファイルを親のフォルダまでさかのぼって読み、結果ファイルと fail_on も設定から取る', async () => {
  const dir = tmp({
    'repo/testcase-lint.config.yaml': 'cases: [qa]\nevidence: out/result.json\nfail_on: never\n',
    'repo/qa/a.yaml': '- {id: A, title: a, steps: [押す], expected: [TBD]}\n',
    'repo/sub/deep/.keep': '',
  });
  const r = await inDir(require('path').join(dir, 'repo', 'sub', 'deep'), []);
  assert.equal(r.code, 0);
  assert.match(r.out, /D07/);
  assert.match(r.out, /testcase-lint\.config\.yaml/);
  const ev = JSON.parse(fs.readFileSync(path.join(dir, 'repo', 'out', 'result.json'), 'utf8'));
  assert.equal(ev.items.find((x) => x.id === 'testcase-lint/errors').value, 1);
});

test('オプション無し: 点検するものが無ければ init を勧めて 2 で終わる', async () => {
  const r = await inDir(tmp({ 'README.md': 'x' }), []);
  assert.equal(r.code, 2);
  assert.match(r.err, /testcase-lint init/);
});

test('init: 見つけたフォルダで設定ファイルを作り、既存のものは上書きしない', async () => {
  const dir = tmp({ 'tests/e2e/a.yaml': '- {id: A, title: a, steps: [押す], expected: [出る]}\n', 'docs/specs/x.md': '# x\n' });
  let r = await inDir(dir, ['init', '--source', '../app/src']);
  assert.equal(r.code, 0);
  const conf = fs.readFileSync(path.join(dir, 'testcase-lint.config.yaml'), 'utf8');
  assert.match(conf, /^cases: \[tests\/e2e\]/m);
  assert.match(conf, /^spec: \[docs\/specs\]/m);
  assert.match(conf, /^source: \[\.\.\/app\/src\]/m);
  fs.appendFileSync(path.join(dir, 'testcase-lint.config.yaml'), '# 手で足した\n');
  r = await inDir(dir, ['init']);
  assert.match(r.out, /上書きしません/);
  assert.match(fs.readFileSync(path.join(dir, 'testcase-lint.config.yaml'), 'utf8'), /手で足した/);
});
