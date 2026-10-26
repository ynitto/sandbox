'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { temporaryFindings } = require('../src/temporary');
const { loadFile, FieldMap } = require('../src/load');
const { tmp, cli } = require('./helpers');

const imports = "const test = require('node:test'); const assert = require('node:assert/strict');\n";
const directive = (meta) => `// testcase-lint-temporary: ${JSON.stringify(meta)}\n`;
const declaration = "test('送料を返す', () => { assert.equal(shipping(5000), 0); });\n";
const base = { file: 'tests/shipping.test.js', line: 3, title: '送料', fullTitle: '送料' };
const at = (tests, date = '2026-10-25T10:00:00Z') => temporaryFindings(tests, { now: new Date(date) });
const codes = (findings) => findings.map((f) => f.code);

test('暫定: タイトル・タグ・本文の明確な暫定表現を未管理として報告する', () => {
  assert.deepEqual(codes(at([{ ...base, title: '暫定実装の送料' }, { ...base, tags: ['@temporary'] },
    { ...base, temporarySignals: ['TODO: remove workaround when API is ready'] },
    { ...base, fullTitle: '暫定仕様 > 送料' }, { ...base, fullTitle: 'test_temporary_implementation' }])), ['T01', 'T01', 'T01', 'T01', 'T01']);
  assert.deepEqual(at([{ ...base, title: 'temporary directory cleanup' }, { ...base, title: 'mock server' },
    { ...base, title: 'fallback retry' }]), []);
});

test('暫定: 期限はUTCの日付の終わりまで有効で、次の日から取り残しを指摘する', () => {
  const t = { ...base, temporary: { reason: 'API公開までの仮仕様', expires_on: '2026-10-25' } };
  assert.deepEqual(at([t], '2026-10-25T23:59:59Z'), []);
  const findings = at([t], '2026-10-26T00:00:00Z');
  assert.deepEqual(codes(findings), ['T02']);
  assert.equal(findings[0].verdict, 'review-retirement');
});

test('暫定: 実装の削除・マーカー消失と、単なる内容変更を区別する', () => {
  const dir = tmp({ 'src/shipping.js': '// TEMP-SHIPPING\nexports.shipping = () => 0;\n' });
  const t = { ...base, file: path.join(dir, 'tests/shipping.test.js'), temporary: { reason: '料金計算の実装待ち',
    implementation: { file: '../src/shipping.js', marker: 'TEMP-SHIPPING' } } };
  assert.deepEqual(at([t]), []);
  t.temporary.implementation.sha256 = createHash('sha256').update(fs.readFileSync(path.join(dir, 'src/shipping.js'))).digest('hex');
  fs.appendFileSync(path.join(dir, 'src/shipping.js'), '// changed\n');
  assert.deepEqual(codes(at([t])), ['T03']);
  fs.writeFileSync(path.join(dir, 'src/shipping.js'), 'exports.shipping = total => total >= 5000 ? 0 : 500;\n');
  assert.deepEqual(codes(at([t])), ['T02']);
  fs.unlinkSync(path.join(dir, 'src/shipping.js'));
  assert.deepEqual(codes(at([t])), ['T02']);
});

test('暫定: 壊れた管理情報を見逃さず、恒久的な回帰テストには理由を求める', () => {
  for (const temporary of [null, {}, { reason: ' ' }, { reason: 'api' }, { reason: 'api', expires_on: '2026-02-30' },
    { reason: 'api', expires_on: '2026-13-01' }, { reason: 'api', implementation: { file: 'src/a.js' } },
    { reason: 'api', implementation: { file: 'src/a.js', marker: ' ' } },
    { reason: 'api', status: 'permanent', expires_on: '2026-10-25' }]) {
    assert.deepEqual(codes(at([{ ...base, temporary }])), ['T04'], JSON.stringify(temporary));
  }
  assert.deepEqual(at([{ ...base, title: '暫定対応の再発防止', temporary: { status: 'permanent', reason: '正式仕様でも同じ障害の再発を防ぐ' } }]), []);
});

test('暫定CLI: テストが通り続けても、正式実装への置き換え後に暫定テストの残留を検知する', async () => {
  const meta = { reason: '仮実装で固定した送料を確認', implementation: { file: '../src/shipping.cjs', marker: 'TEMP-SHIPPING' } };
  const dir = tmp({ 'src/shipping.cjs': '// TEMP-SHIPPING\nexports.shipping = () => 0;\n',
    'tests/shipping.test.cjs': imports + "const { shipping } = require('../src/shipping.cjs');\n" + directive(meta) + declaration });
  const file = path.join(dir, 'tests/shipping.test.cjs');
  const runTest = () => spawnSync(process.execPath, ['--test', file], { encoding: 'utf8' });
  assert.equal(runTest().status, 0);
  assert.equal((await cli(['code', file])).code, 0);
  fs.writeFileSync(path.join(dir, 'src/shipping.cjs'), 'exports.shipping = total => total >= 5000 ? 0 : 500;\n');
  assert.equal(runTest().status, 0, '古いテストそのものは依然として成功する');
  const evidence = path.join(dir, 'results/check.json');
  const result = await cli(['code', file, '-f', 'json', '--evidence', evidence]);
  assert.equal(result.code, 1, result.err);
  const data = JSON.parse(result.out);
  assert.equal(data.analysis.complete, true);
  assert.ok(data.findings.some((f) => f.code === 'T02'));
  assert.deepEqual(data.tests[0].temporary, meta);
  const check = JSON.parse(fs.readFileSync(evidence, 'utf8'));
  assert.equal(check.purpose, 'check');
  assert.equal(check.items.find((i) => i.kind === 'behavior').status, 'failed');
});

test('暫定CLI: JSの文字列をコメントと誤認せず、コメントは直後のテストに結び付ける', async () => {
  const dir = tmp({ 'a.test.js': imports +
    'const example = "// testcase-lint-temporary: invalid";\n' +
    "// workaround until the API is ready\ntest('a', () => { assert.equal(1, 1); });\n" +
    "test('b', () => { assert.equal(2, 2); });\n" });
  const result = await cli(['code', path.join(dir, 'a.test.js'), '-f', 'json']);
  const findings = JSON.parse(result.out).findings.filter((f) => f.code.startsWith('T'));
  assert.deepEqual(findings.map((f) => [f.code, f.cases]), [['T01', ['a']]]);
  assert.equal(result.code, 0, '未管理は既定warning、失敗の閾値は既定error');
  assert.equal((await cli(['code', path.join(dir, 'a.test.js'), '--fail-on', 'warning'])).code, 1);
});

test('暫定CLI: Pythonのデコレーターの前に書け、docstringの指示は実行しない', async () => {
  const dir = tmp({ 'test_shipping.py': 'import pytest\n' +
    'example = """# testcase-lint-temporary: invalid"""\n' +
    '# testcase-lint-temporary: {"reason":"公開まで", "expires_on":"2000-01-01"}\n' +
    '@pytest.mark.parametrize("total", [5000])\ndef test_shipping(total):\n    assert total == 5000\n' });
  const result = await cli(['code', path.join(dir, 'test_shipping.py'), '-f', 'json']);
  assert.equal(result.code, 1, result.err);
  assert.deepEqual(JSON.parse(result.out).findings.filter((f) => f.code.startsWith('T')).map((f) => [f.code, f.line]), [['T02', 5]]);
});

test('暫定CLI: 管理コメントのJSON不備と重複をT04として報告する', async () => {
  for (const comments of ['// testcase-lint-temporary: invalid\n', directive({ reason: 'api', expires_on: '2099-01-01' }).repeat(2)]) {
    const dir = tmp({ 'a.test.js': imports + comments + "test('a', () => { assert.equal(1, 1); });\n" });
    const result = await cli(['code', path.join(dir, 'a.test.js'), '-f', 'json']);
    assert.equal(result.code, 1);
    assert.ok(JSON.parse(result.out).findings.some((f) => f.code === 'T04'));
  }
});

test('暫定ケース: JSON/YAMLの管理情報を前提に混ぜず、ケースからの相対で実装を読む', async () => {
  const dir = tmp({ 'cases/shipping.yaml': 'cases:\n  - id: SHIP-01\n    title: 送料\n    steps: [送料を計算する]\n    expected: [送料は0円になる]\n    temporary:\n      reason: 公開まで\n      implementation: {file: ../src/shipping.py, marker: TEMP-SHIPPING}\n',
    'src/shipping.py': 'def shipping(total):\n    return 0 if total >= 5000 else 500\n' });
  const filename = path.join(dir, 'cases/shipping.yaml');
  const loaded = loadFile(filename, filename, new FieldMap());
  assert.deepEqual(loaded.cases[0].preconditions, []);
  const result = await cli([filename, '-f', 'json']);
  assert.equal(result.code, 1);
  const finding = JSON.parse(result.out).findings.find((f) => f.code === 'T02');
  assert.deepEqual(finding.cases, ['SHIP-01']);
  assert.equal(finding.line, 2);
});

test('暫定CLI: 他言語の中間表現でも同じ期限・実装の規則を使える', async () => {
  const dir = tmp({ 'ir.json': JSON.stringify({ version: 1, tests: [{ ...base, framework: 'go', comparable: true, staticTitle: true,
    context: 'suite', body: { exact: 'body', structural: 'shape' }, temporary: { reason: 'API公開まで', expires_on: '2000-01-01' } }] }) });
  const result = await cli(['code', '--ir', path.join(dir, 'ir.json'), '-f', 'json']);
  assert.equal(result.code, 1, result.err);
  assert.ok(JSON.parse(result.out).findings.some((f) => f.code === 'T02'));
});

test('暫定ケース: .feature の @temporary は元の行で T01 になり、そのファイルの証拠を落とす', async () => {
  const dir = tmp({ 'tests/shipping.feature': '# language: ja\n機能: 送料\n  @case:SHIP-01 @temporary\n  シナリオ: 固定値\n    もし 送料を計算する\n    ならば 送料は0円になる\n' });
  const evidence = path.join(dir, 'results/check.json');
  const result = await cli([path.join(dir, 'tests/shipping.feature'), '-f', 'json', '--evidence', evidence, '--fail-on', 'warning']);
  assert.equal(result.code, 1, result.err);
  const finding = JSON.parse(result.out).findings.find((f) => f.code === 'T01');
  assert.equal(finding.file, path.join(dir, 'tests/shipping.feature'));
  assert.equal(finding.line, 4);
  assert.equal(JSON.parse(fs.readFileSync(evidence, 'utf8')).items.find((i) => i.kind === 'behavior').status, 'failed');
});

test('暫定ケース: 推奨JSON Schemaと同じメタデータで検証できる', async () => {
  const schema = path.join(__dirname, '../schema/testcase.schema.json');
  const dir = tmp({ 'cases.json': JSON.stringify({ cases: [{ id: 'TC-01', title: '送料', steps: ['送料を計算する'], expected: ['送料は0円になる'],
    temporary: { reason: '正式仕様でも再発を防ぐ', status: 'permanent' } }] }), 'config.yaml': `format: {schema: ${JSON.stringify(schema)}}\n` });
  const result = await cli([path.join(dir, 'cases.json'), '-c', path.join(dir, 'config.yaml'), '-f', 'json']);
  assert.equal(result.code, 0, result.err);
  assert.ok(!JSON.parse(result.out).findings.some((f) => f.code === 'D16' || f.code.startsWith('T')));
});

test('暫定CLI: 出力で参照先の実装を壊さず、規則の設定を反映する', async () => {
  const source = '// TEMP-SHIPPING\nexports.shipping = () => 0;\n';
  const meta = { reason: 'API公開まで', implementation: { file: 'impl.js', marker: 'gone' } };
  const dir = tmp({ 'a.test.js': imports + directive(meta) + "test('a', () => { assert.equal(1, 1); });\n",
    'cases.json': JSON.stringify({ cases: [{ id: 'TC-01', title: '送料', steps: ['計算する'], expected: ['送料は0円になる'], temporary: meta }] }),
    'impl.js': source, 'config.yaml': 'rules: {T02: off}\n' });
  for (const args of [['code', path.join(dir, 'a.test.js')], [path.join(dir, 'cases.json')]]) {
    for (const flag of ['-o', '--evidence']) assert.equal((await cli([...args, flag, path.join(dir, 'impl.js')])).code, 2);
  }
  assert.equal(fs.readFileSync(path.join(dir, 'impl.js'), 'utf8'), source);
  assert.equal((await cli(['code', path.join(dir, 'a.test.js'), '-c', path.join(dir, 'config.yaml')])).code, 0);
});
