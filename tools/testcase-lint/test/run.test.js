'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { tmp, cli, byCode } = require('./helpers');
const { parseJUnit } = require('../src/run');

const xml = ({ content = '', name = 'test_ok', status = '' } = {}) =>
  `<testsuites><testsuite><testcase classname="TestApp" name="${name}"${status}>${content}</testcase></testsuite></testsuites>`;

// JUnit を標準出力（report 指定時はファイル）に書いて code で終わるテストのコマンドを持つ、小さなプロジェクト
function project({ report = false, bindings, run } = {}) {
  const dir = tmp({
    'cases.yaml': 'cases:\n  - { id: TC-01, title: ok, steps: [呼ぶ], expected: [「1」を返す] }\n  - { id: TC-02, title: other, steps: [呼ぶ], expected: [「2」を返す] }\n',
    'test_app.py': '# test source\n',
    'emit.js': `const fs = require('fs');
const [xml, code, mutate, report] = [process.env.XML, Number(process.env.CODE || 0), process.env.MUTATE, process.env.REPORT];
if (mutate) fs.writeFileSync('test_app.py', 'changed');
if (report) fs.writeFileSync(report, xml); else process.stdout.write(xml);
process.exitCode = code;
`,
    'bindings.json': JSON.stringify(bindings || { version: 1, bindings: [{ case: { file: 'cases.yaml', id: 'TC-01' },
      targets: [{ suite: 'unit', file: 'test_app.py', selector: 'TestApp::test_ok' }] }] }, null, 2),
    'testcase-lint.config.yaml': `cases: [cases.yaml]\nbindings: bindings.json\nrun:\n  unit: { command: [${JSON.stringify(process.execPath)}, emit.js]${report ? ', junit: report.xml' : ''} }\n${run || ''}`,
  });
  return dir;
}

async function runIn(dir, env = {}, args = []) {
  const saved = { cwd: process.cwd(), env: { ...process.env } };
  process.chdir(dir);
  Object.assign(process.env, { TEST_EVIDENCE_RUN_ID: 'current', TEST_EVIDENCE_INPUTS_DIGEST: 'inputs' }, env);
  try { return await cli(['run', ...args]); } finally {
    process.chdir(saved.cwd);
    for (const k of Object.keys(process.env)) if (!(k in saved.env)) delete process.env[k];
    Object.assign(process.env, saved.env);
  }
}
const result = (dir, suite = 'unit') => JSON.parse(fs.readFileSync(path.join(dir, `results/${suite}.json`), 'utf8'));

test('run: 対応先の結果だけを、ケースとテストのハッシュ付きで書く', async () => {
  const dir = project({ report: true });
  const r = await runIn(dir, { XML: xml(), REPORT: 'report.xml' });
  assert.equal(r.code, 0, r.err);
  const data = result(dir);
  assert.deepEqual([data.purpose, data.run_id, data.inputs_digest, data.root], ['execution', 'current', 'inputs', '..']);
  assert.equal(data.items[0].id, 'TestApp::test_ok');
  assert.equal(data.items[0].cases[0].id, 'TC-01');
  assert.match(data.items[0].test_sha256, /^[a-f0-9]{64}$/);
});

test('run: skip・error・failure・別のテスト・空・ランナーの失敗・notrun・未知の status は通さない', async () => {
  for (const [body, code] of [[xml({ content: '<skipped/>' }), 0], [xml({ content: '<error/>' }), 0], [xml({ content: '<failure/>' }), 0],
    [xml({ name: 'other' }), 0], ['<testsuites/>', 0], [xml(), 1], [xml({ status: ' status="notrun"' }), 0], [xml({ status: ' status="unknown"' }), 0]]) {
    const r = await runIn(project(), { XML: body, CODE: String(code) });
    assert.notEqual(r.code, 0, body);
  }
});

test('run: 実行中に入力が変われば結果を書かない', async () => {
  const dir = project();
  const r = await runIn(dir, { XML: xml(), MUTATE: '1' });
  assert.equal(r.code, 2);
  assert.match(r.err, /入力が変わりました/);
  assert.equal(fs.existsSync(path.join(dir, 'results/unit.json')), false);
});

test('run: 失敗の理由を結果に残す', async () => {
  const dir = project();
  const r = await runIn(dir, { XML: xml({ content: '<failure message="expected 0, got 500"/>' }), CODE: '1' });
  assert.equal(r.code, 1);
  assert.equal(result(dir).items[0].error, 'expected 0, got 500');
  assert.match(r.err, /expected 0, got 500/);
});

test('run: 古い JUnit と古い結果を使わない', async () => {
  const dir = project({ report: true });
  fs.writeFileSync(path.join(dir, 'report.xml'), xml());
  fs.mkdirSync(path.join(dir, 'results'));
  fs.writeFileSync(path.join(dir, 'results/unit.json'), '{"old": true}');
  const r = await runIn(dir, { XML: '' });
  assert.equal(r.code, 2);
  assert.equal(fs.existsSync(path.join(dir, 'results/unit.json')), false);
});

test('run: JUnit に file が無い同名テストは特定できないので止める', async () => {
  const dir = project({ bindings: { version: 1, bindings: [{ case: { file: 'cases.yaml', id: 'TC-01' }, targets: [
    { suite: 'unit', file: 'test_app.py', selector: 'TestApp::test_ok' }, { suite: 'unit', file: 'emit.js', selector: 'TestApp::test_ok' }] }] } });
  assert.match((await runIn(dir, { XML: xml() })).err, /特定できません/);
});

test('run: 1 つのテストで複数のケースを確かめられる', async () => {
  const target = [{ suite: 'unit', file: 'test_app.py', selector: 'TestApp::test_ok' }];
  const dir = project({ bindings: { version: 1, bindings: [{ case: { file: 'cases.yaml', id: 'TC-01' }, targets: target }, { case: { file: 'cases.yaml', id: 'TC-02' }, targets: target }] } });
  assert.equal((await runIn(dir, { XML: xml() })).code, 0);
  assert.equal(result(dir).items[0].cases.length, 2);
});

test('run: 設定に無い suite・run の無い設定は使い方の誤り', async () => {
  assert.equal((await runIn(project(), { XML: xml() }, ['e2e'])).code, 2);
  const dir = project();
  fs.writeFileSync(path.join(dir, 'testcase-lint.config.yaml'), 'cases: [cases.yaml]\nbindings: bindings.json\n');
  assert.match((await runIn(dir)).err, /run に/);
});

test('JUnit: 実体参照・CDATA・名前空間を読み、DTD と崩れた XML は受けない', () => {
  const [r] = parseJUnit('<?xml version="1.0"?>\n<!-- c --><x:testsuite xmlns:x="u"><x:testcase name="a&amp;b" file="t.js"><x:failure><![CDATA[got <1>]]></x:failure></x:testcase></x:testsuite>');
  assert.deepEqual([...r.selectors], ['a&b']);
  assert.equal(r.file, 't.js');
  assert.equal(r.status, 'failed');
  assert.equal(r.detail, 'got <1>');
  assert.throws(() => parseJUnit('<!DOCTYPE x><testsuite/>'), /DTD/);
  assert.throws(() => parseJUnit('<testsuite><testcase name="a"></testsuite>'), /XML/);
  assert.throws(() => parseJUnit(''), /XML/);
});

test('B01〜B03: 対応表のケース・対応先のテスト・対応していないケースを点検する', async () => {
  const dir = project({ bindings: { version: 1, bindings: [
    { case: { file: 'cases.yaml', id: 'TC-01' }, targets: [{ suite: 'unit', file: 'test_app.py', selector: 'TestApp::test_ok' }] },
    { case: { file: 'cases.yaml', id: 'TC-09' }, targets: [{ suite: 'unit', file: 'test_app.py', selector: 'x' }] },
    { case: { file: 'gone.yaml', id: 'TC-01' }, targets: [{ suite: 'e2e', file: 'missing.test.js', selector: 'y' }] },
  ] } });
  const saved = process.cwd();
  process.chdir(dir);
  let r;
  try { r = await cli(['-f', 'json']); } finally { process.chdir(saved); }
  const findings = JSON.parse(r.out).findings;
  assert.deepEqual(byCode(findings, 'B01').map((f) => f.message), ['cases.yaml にケース TC-09 がありません', 'ケースのファイル gone.yaml がありません（TC-01）']);
  assert.deepEqual(byCode(findings, 'B02').map((f) => f.message), ['TC-01 の対応先のテスト missing.test.js がありません', 'suite「e2e」が設定の run にありません（TC-01）']);
  assert.deepEqual(byCode(findings, 'B03').map((f) => f.cases), [['TC-02']]);
  assert.ok(byCode(findings, 'B01').every((f) => f.file === 'bindings.json' && f.line > 1));
  assert.equal(r.code, 1);
});
