'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { createHash } = require('node:crypto');
const { tmp, cli } = require('./helpers');
const { loadFile, FieldMap } = require('../src/load');

function fixture() {
  const text = 'source line 1\nsource line 2\nsource line 3\n';
  const dir = tmp({ 'source.txt': text });
  const origin = { file: '../source.txt', line: 2, input_sha256: createHash('sha256').update(text).digest('hex'), fields: { steps: [2], expected: [3] } };
  const content = { cases: [{ id: 'TC-01', title: '計算', steps: ['送料を計算する'], expected: ['TBD'], group: ['送料'], origin }] };
  fs.mkdirSync(path.join(dir, 'generated'));
  fs.writeFileSync(path.join(dir, 'generated/cases.json'), JSON.stringify(content));
  return { dir, content };
}

test('一般的な入力元情報を元の位置へ戻し、メタデータを前提に加えない', () => {
  const { dir } = fixture();
  const result = loadFile(path.join(dir, 'generated/cases.json'), 'generated/cases.json', new FieldMap());
  assert.deepEqual(result.findings, []);
  const c = result.cases[0];
  assert.equal(c.file, path.join(dir, 'source.txt'));
  assert.equal(c.line, 2);
  assert.equal(c.expected[0].line, 3);
  assert.deepEqual(c.preconditions, []);
  assert.deepEqual(c.group, ['送料']);
  assert.equal(c.input_sha256, c.origin.input_sha256);
});

test('古い生成物や壊れた位置情報はD00になり、素のJSON/YAMLは従来どおり読める', () => {
  const { dir, content } = fixture();
  fs.writeFileSync(path.join(dir, 'source.txt'), 'changed');
  let result = loadFile(path.join(dir, 'generated/cases.json'), 'generated/cases.json', new FieldMap());
  assert.ok(result.findings.some((f) => f.code === 'D00'));
  delete content.cases[0].origin;
  fs.writeFileSync(path.join(dir, 'generated/cases.json'), JSON.stringify(content));
  result = loadFile(path.join(dir, 'generated/cases.json'), 'generated/cases.json', new FieldMap());
  assert.equal(result.findings.length, 0);
  assert.equal(result.cases[0].file, 'generated/cases.json');
});

test('JSON出力のケース一覧とlint証拠は入力元の指摘を取りこぼさない', async () => {
  const { dir } = fixture();
  const report = path.join(dir, 'check.json');
  const result = await cli([path.join(dir, 'generated/cases.json'), '-f', 'json', '--evidence', report]);
  assert.equal(result.code, 1);
  const data = JSON.parse(result.out);
  assert.equal(data.cases.length, 1);
  assert.match(data.cases[0].input_sha256, /^[a-f0-9]{64}$/);
  const finding = data.findings.find((f) => f.code === 'D07');
  assert.equal(finding.file, path.join(dir, 'source.txt'));
  assert.equal(finding.line, 3);
  assert.equal(JSON.parse(fs.readFileSync(report, 'utf8')).items.find((i) => i.kind === 'behavior').status, 'failed');
});

test('スキーマの指摘も元のステップへ戻す', async () => {
  const { dir } = fixture();
  fs.writeFileSync(path.join(dir, 'schema.json'), JSON.stringify({ type: 'object', properties: { cases: { type: 'array', items: {
    type: 'object', properties: { expected: { type: 'array', items: { const: '送料は0円になる' } } },
  } } } }));
  fs.writeFileSync(path.join(dir, 'config.yaml'), 'format: { schema: schema.json }\n');
  const result = await cli([path.join(dir, 'generated/cases.json'), '-c', path.join(dir, 'config.yaml'), '-f', 'json']);
  const finding = JSON.parse(result.out).findings.find((f) => f.code === 'D16');
  assert.equal(finding.file, path.join(dir, 'source.txt'));
  assert.equal(finding.line, 3);
});
