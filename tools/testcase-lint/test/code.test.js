'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { javascript } = require('../src/code/javascript');
const { analyze } = require('../src/code/analyze');
const { defaults } = require('../src/config');
const { cli, tmp } = require('./helpers');

const imports = "import {test, it, describe, beforeEach, expect, vi} from 'vitest';\n";
function js(source, options = {}, file = 'code.test.ts') {
  const data = javascript(source, file, path.resolve('/project', file), options);
  return { ...data, findings: analyze(data.tests, data.diagnostics, defaults()) };
}
const codes = (data) => new Set(data.findings.map((f) => f.code));
const same = "expect(sum(1)).toBe(2)";

test('コード: タイトル・コメント・ローカル変数名を変えても本文の重複を検出する', () => {
  const data = js(imports + "test('a',()=>{const value=sum(1);expect(value).toBe(2)});\ntest('b',()=>{/* comment */const other=sum(1);expect(other).toBe(2)});");
  assert.ok(codes(data).has('K01'));
  assert.ok(!codes(data).has('K02'));
  assert.deepEqual(data.findings[0].cases, ['a', 'b']);
});

test('コード: 境界値の違いは削除でなく各行を残す統合候補になる', () => {
  const data = js(imports + "test('below',()=>expect(shipping(4999)).toBe(500));test('at',()=>expect(shipping(5000)).toBe(0));");
  assert.ok(!codes(data).has('K01'));
  const finding = data.findings.find((f) => f.code === 'K02');
  assert.equal(finding.verdict, 'consolidate-keep-cases');
  assert.match(finding.hint, /各ケースを独立した行/);
  assert.equal(data.tests.length, 2);
});

test('コード: 異なる演算・アサーション・プロパティ名を値違いとしてまとめない', () => {
  for (const [a, b] of [["expect(a + 1).toBe(2)", "expect(a - 1).toBe(2)"],
    ["expect(a).toBe(2)", "expect(a).toEqual(2)"],
    ["const foo=1;expect({foo}).toEqual({foo:1})", "const bar=1;expect({bar}).toEqual({bar:1})"],
    ["expect(obj['a']).toBe(2)", "expect(obj['b']).toBe(2)"]]) {
    assert.equal(codes(js(imports + `test('a',()=>{${a}});test('b',()=>{${b}});`)).size, 0, `${a} / ${b}`);
  }
});

test('コード: hook・mock・外側の束縛の違いを保持する', () => {
  for (const setup of [
    ["beforeEach(()=>reset('a'))", "beforeEach(()=>reset('b'))"],
    ["vi.mock('api',()=>({value:1}))", "vi.mock('api',()=>({value:2}))"],
    ["const value=1", "const value=2"],
  ]) {
    const data = js(imports + `describe('a',()=>{${setup[0]};test('first',()=>{${same}})});describe('b',()=>{${setup[1]};test('second',()=>{${same}})});`);
    assert.ok(!codes(data).has('K01'));
    assert.ok(!codes(data).has('K02'));
  }
  const data = js(imports + "describe('a',()=>{const value=1;test('a',()=>expect(value).toBe(1))});describe('b',()=>{const value=1;test('b',()=>expect(value).toBe(1))});");
  assert.ok(!codes(data).has('K01'));
});

test('コード: callbackのfixture名・extendしたprovider・修飾子を混同しない', () => {
  const data = js("import {test,expect} from '@playwright/test';\n" +
    "test('a',async({page})=>{await page.run()});test('b',async({request})=>{await request.run()});");
  assert.ok(!codes(data).has('K01'));
  assert.ok(!codes(data).has('K02'));
  const extended = js(imports + `const a=test.extend({role:'a'});const b=test.extend({role:'b'});a('a',()=>{${same}});b('b',()=>{${same}});`);
  assert.ok(!codes(extended).has('K01'));
  const skipped = js(imports + `test.skip('a',()=>{${same}});test('b',()=>{${same}});`);
  assert.ok(!codes(skipped).has('K01'));
  const opts = js("import test from 'node:test';test('a',{timeout:100},()=>{verify()});test('b',{timeout:200},()=>{verify()});");
  assert.ok(!codes(opts).has('K01'));
});

test('コード: タイトルの観測と登録間の初期化を無視しない', () => {
  const data = js(imports + `beforeEach(function(){set(this.currentTest.title)});test('a',()=>{${same}});test('b',()=>{${same}});`);
  assert.ok(!codes(data).has('K01'));
  const ordered = js(imports + `test('a',()=>{${same}});changeEnvironment();test('b',()=>{${same}});`);
  assert.ok(!codes(ordered).has('K01'));
});

test('コード: 同名テストはsuiteを含む完全名で比較する', () => {
  const data = js(imports + "describe('a',()=>{test('same',()=>expect(1).toBe(1));test('same',()=>expect(2).toBe(2))});describe('b',()=>{test('same',()=>expect(3).toBe(3))});");
  const names = data.findings.filter((f) => f.code === 'K03');
  assert.equal(names.length, 1);
  assert.equal(names[0].cases.length, 2);
});

test('コード: eachの重複行を報告し、異なる表の宣言を重複扱いしない', () => {
  const data = js(imports + "test.each([[1,2],[1,2],[2,3]])('value %i',(x,y)=>expect(x).toBe(y));");
  assert.ok(codes(data).has('K05'));
  const tables = js(imports + "test.each([[1,2]])('value %i',(x,y)=>expect(x).toBe(y));test.each([[2,3]])('value %i',(x,y)=>expect(x).toBe(y));");
  assert.equal(codes(tables).size, 0);
});

test('コード: ローカル定数の表は読めるが、変更・受け渡しのある表を推測しない', () => {
  const known = js(imports + "const cases=[[1,2],[1,2]];test.each(cases)('x',(a,b)=>expect(a).toBe(b));");
  assert.ok(codes(known).has('K05'));
  for (const use of ['cases.push([2,3]);', 'mutate(cases);', 'mutator.each(cases);']) {
    const data = js(imports + `const cases=[[1,2],[1,2]];${use}test.each(cases)('x',(a,b)=>expect(a).toBe(b));`);
    assert.ok(data.diagnostics.length);
    assert.ok(!codes(data).has('K05'));
  }
});

test('コード: 動的な登録や空の表は未解析として示す', () => {
  for (const source of ["test.each(loadCases())('x',x=>expect(x).toBe(1));", "test.each([])('x',x=>expect(x).toBe(1));",
    "for(const x of [1,2]) test('x',()=>expect(x).toBe(1));", "test(makeTitle(),()=>expect(1).toBe(1));"]) {
    const data = js(imports + source);
    assert.ok(data.diagnostics.length, source);
    assert.ok(!codes(data).has('K01'), source);
  }
});

test('コード: 通常のテストがあっても未対応のタグ付き表を黙って捨てない', () => {
  const data = js(imports + "test('known',()=>expect(1).toBe(1));test.each`a | b\n${1} | ${2}`('row',(x)=>expect(x).toBe(1));");
  assert.ok(data.diagnostics.some((d) => d.reason.includes('タグ付き')));
});

test('コード: 反射的処理と関数名の観測でローカル名を消さない', () => {
  const reflected = js(imports + "test('a',()=>{const a=1;eval('a')});test('b',()=>{const b=1;eval('a')});");
  assert.ok(reflected.diagnostics.length);
  assert.ok(!codes(reflected).has('K01'));
  const named = js(imports + "test('same',()=>{function a(){};inspect(a)});test('same',()=>{function b(){};inspect(b)});");
  assert.ok(!codes(named).has('K01'));
});

test('コード: Node・Jest・Playwrightのimportを読み、globalsは明示時だけ使う', () => {
  for (const source of [
    "const test=require('node:test');test('a',()=>{assert.equal(1,1)});test('b',()=>{assert.equal(1,1)});",
    "import {test as check,expect} from '@jest/globals';check('a',()=>expect(1).toBe(1));check('b',()=>expect(1).toBe(1));",
    "import {test,expect} from '@playwright/test';test.describe('suite',()=>{test('a',async({page})=>{await page.goto('/a')});test('b',async({page})=>{await page.goto('/a')})});",
    "import test from 'node:test';await test('a',()=>{assert.equal(1,1)});await test('b',()=>{assert.equal(1,1)});",
  ]) assert.ok(codes(js(source)).has('K01'), source);
  const source = `test('a',()=>{${same}});test('b',()=>{${same}});`;
  assert.equal(js(source).tests.length, 0);
  assert.ok(codes(js(source, { globals: true })).has('K01'));
  assert.equal(js("function test(){};" + source, { globals: true }).tests.length, 0);
});

test('コード: 別ファイルの一致は検証する層を確認する指摘にする', () => {
  const source = imports + `test('a',()=>{${same}});`;
  const a = js(source, {}, 'a.test.ts'); const b = js(source, {}, 'b.test.ts');
  const findings = analyze([...a.tests, ...b.tests], [], defaults());
  assert.equal(findings[0].code, 'K04');
  assert.match(findings[0].hint, /単体・結合・E2E/);
});

function py(source) {
  const cfg = defaults();
  const result = spawnSync(cfg.code.python, [path.resolve(__dirname, '../src/code/python.py')], {
    input: JSON.stringify({ source, file: 'test_code.py' }), encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const data = JSON.parse(result.stdout);
  return { ...data, findings: analyze(data.tests, data.diagnostics, cfg) };
}

test('コード/Python: ローカル名の違う同じ本文と、値だけの違いを区別する', () => {
  const data = py('def test_a():\n    value = add(1)\n    assert value == 2\n\ndef test_b():\n    other = add(1)\n    assert other == 2\n\ndef test_c():\n    other = add(2)\n    assert other == 3\n');
  assert.ok(codes(data).has('K01'));
  assert.ok(codes(data).has('K02'));
  assert.equal(data.diagnostics.length, 0);
});

test('コード/Python: fixture引数・setup・デコレーター・継承が違えば同一扱いしない', () => {
  assert.ok(!codes(py('def test_a(admin):\n    assert admin.run() == 1\ndef test_b(member):\n    assert member.run() == 1\n')).has('K01'));
  const data = py('import unittest\nclass TestA(unittest.TestCase):\n    def setUp(self):\n        self.value=1\n    def test_a(self):\n        self.assertEqual(self.value,1)\nclass TestB(unittest.TestCase):\n    def setUp(self):\n        self.value=2\n    def test_b(self):\n        self.assertEqual(self.value,1)\n');
  assert.equal(codes(data).size, 0);
  assert.ok(py('class TestA(CustomCase):\n    def test_a(self):\n        assert value == 1\n').diagnostics.length);
});

test('コード/Python: parametrizeの重複行と動的な表を区別する', () => {
  const data = py('import pytest\n@pytest.mark.parametrize("a,b", [(1,2),(1,2),(2,3)])\ndef test_a(a,b):\n    assert add(a) == b\n');
  assert.ok(codes(data).has('K05'));
  const dynamic = py('import pytest\n@pytest.mark.parametrize("a,b", load_cases())\ndef test_a(a,b):\n    assert a == b\n');
  assert.ok(dynamic.diagnostics.length);
  assert.ok(!codes(dynamic).has('K05'));
  const constant = py('import pytest\nCASES=[(1,2),(1,2)]\n@pytest.mark.parametrize("a,b", CASES)\ndef test_a(a,b):\n    assert a == b\n');
  assert.ok(codes(constant).has('K05'));
  const mutated = py('import pytest\nCASES=[(1,2),(1,2)]\nCASES.append((2,3))\n@pytest.mark.parametrize("a,b", CASES)\ndef test_a(a,b):\n    assert a == b\n');
  assert.ok(mutated.diagnostics.length);
  assert.ok(!codes(mutated).has('K05'));
  const alias = py('import pytest\nCASES=OTHER=[(1,2),(1,2)]\nOTHER.append((2,3))\n@pytest.mark.parametrize("a,b", CASES)\ndef test_a(a,b):\n    assert a == b\n');
  assert.ok(alias.diagnostics.length);
});

test('コード/Python: タイトルに依存するfixtureやsetUpを無視しない', () => {
  const data = py('import unittest\nclass TestA(unittest.TestCase):\n    def setUp(self):\n        load(self._testMethodName)\n    def test_a(self):\n        assert value == 1\n    def test_b(self):\n        assert value == 1\n');
  assert.ok(!codes(data).has('K01'));
});

test('コード/Python: fixture定義をテストにせず、名前の反射や未知のデコレーターを比較しない', () => {
  const fixture = py('import pytest\n@pytest.fixture\ndef test_data():\n    return 1\ndef test_a(test_data):\n    assert test_data == 1\n');
  assert.equal(fixture.tests.length, 1);
  assert.equal(fixture.diagnostics.length, 0);
  assert.ok(py('def test_a():\n    value=1\n    assert locals() == {"value":1}\n').diagnostics.length);
  assert.ok(py('@custom\ndef test_a():\n    assert value == 1\n').diagnostics.length);
});

test('コードCLI: 設定・JSON・ゲート・check証拠を使える', async () => {
  const dir = tmp({ 'src/a.test.ts': imports + `test('a',()=>{${same}});test('b',()=>{${same}});`,
    'config.yaml': 'code:\n  files: [src/**/*.test.ts]\n  evidence: results/code.json\nfail_on: warning\n' });
  const result = await cli(['code', '-c', path.join(dir, 'config.yaml'), '-f', 'json']);
  assert.equal(result.code, 1, result.err);
  const data = JSON.parse(result.out);
  assert.equal(data.analysis.complete, true);
  assert.equal(data.tests.length, 2);
  assert.equal(data.cases, undefined);
  assert.ok(data.findings.some((f) => f.code === 'K01'));
  const evidence = JSON.parse(fs.readFileSync(path.join(dir, 'results/code.json'), 'utf8'));
  assert.equal(evidence.purpose, 'check');
  assert.equal(evidence.items.find((i) => i.kind === 'behavior').status, 'failed');
  assert.equal((await cli(['code', '-c', path.join(dir, 'config.yaml'), '--min-severity', 'error'])).code, 1);
});

test('コードCLI: 明示した対象と除外を使い、除外したfixtureを解析しない', async () => {
  const dir = tmp({ 'tests/a.test.ts': imports + `test('a',()=>{${same}});`, 'tests/fixtures/broken.test.ts': 'invalid (',
    'config.yaml': 'code:\n  files: [tests]\n  exclude: ["**/fixtures/**"]\n' });
  const result = await cli(['code', '-c', path.join(dir, 'config.yaml'), '-f', 'json']);
  assert.equal(result.code, 0, result.err);
  assert.equal(JSON.parse(result.out).summary.files, 1);
  assert.equal(JSON.parse(result.out).analysis.complete, true);
});

test('コードCLI: テストとプロジェクト設定を実行しない', async () => {
  const dir = tmp({
    'a.test.js': "const fs=require('fs');fs.writeFileSync('__SENTINEL__','bad');\n" +
      "const test=require('node:test');test('a',()=>{throw new Error('do not execute')});",
    'test_b.py': "raise RuntimeError('do not execute')\ndef test_x():\n    assert 1 == 1\n",
    'vitest.config.js': "throw new Error('do not execute config');\n",
  });
  const sentinel = path.join(dir, 'executed');
  fs.writeFileSync(path.join(dir, 'a.test.js'), fs.readFileSync(path.join(dir, 'a.test.js'), 'utf8').replace("'__SENTINEL__'", JSON.stringify(sentinel)));
  const before = fs.readFileSync(path.join(dir, 'a.test.js'), 'utf8');
  const result = await cli(['code', dir, '-f', 'json']);
  assert.equal(result.code, 0, result.err);
  assert.equal(JSON.parse(result.out).analysis.tests, 2);
  assert.equal(fs.readFileSync(path.join(dir, 'a.test.js'), 'utf8'), before);
  assert.equal(fs.existsSync(sentinel), false);
});

test('コードCLI: Python起動時にプロジェクトのsitecustomizeも実行しない', async () => {
  const dir = tmp({ 'test_a.py': 'def test_a():\n    assert value == 1\n', 'sitecustomize.py': 'from pathlib import Path\nPath(__file__).with_name("executed").write_text("bad")\n' });
  const previous = process.env.PYTHONPATH;
  process.env.PYTHONPATH = dir;
  try {
    assert.equal((await cli(['code', path.join(dir, 'test_a.py')])).code, 0);
    assert.equal(fs.existsSync(path.join(dir, 'executed')), false);
  } finally {
    if (previous == null) delete process.env.PYTHONPATH; else process.env.PYTHONPATH = previous;
  }
});

test('コードCLI: 不明な表は未解析でexit 2、許可時も不完全と表示する', async () => {
  const dir = tmp({ 'a.test.ts': imports + "test.each(loadCases())('x',x=>expect(x).toBe(1));", 'config.yaml': 'rules: {K00: off}\n' });
  const args = ['code', path.join(dir, 'a.test.ts'), '-c', path.join(dir, 'config.yaml'), '-f', 'json'];
  const result = await cli(args);
  assert.equal(result.code, 2);
  assert.equal(JSON.parse(result.out).analysis.complete, false);
  const allowed = await cli([...args, '--allow-incomplete']);
  assert.equal(allowed.code, 0);
  assert.equal(JSON.parse(allowed.out).analysis.complete, false);
});

test('コードCLI: 意図的なケースは理由付きで抑制し、他の重複は残す', async () => {
  const dir = tmp({ 'a.test.ts': imports + `// testcase-lint-code-ignore-next-line K01: 意図的な並列例\ntest('kept',()=>{${same}});\ntest('a',()=>{${same}});\ntest('b',()=>{${same}});\n` });
  const result = await cli(['code', path.join(dir, 'a.test.ts'), '-f', 'json']);
  const data = JSON.parse(result.out);
  assert.equal(data.analysis.suppressions.length, 1);
  assert.deepEqual(data.findings.find((f) => f.code === 'K01').cases, ['a', 'b']);
  fs.writeFileSync(path.join(dir, 'a.test.ts'), imports + `// testcase-lint-code-ignore-next-line K01\ntest('a',()=>{${same}});`);
  assert.equal((await cli(['code', path.join(dir, 'a.test.ts')])).code, 2);
});

test('コードCLI: 言語に依存しない中間表現も受ける', async () => {
  const unit = { file: 'calc_test.go', line: 3, title: 'A', fullTitle: 'A', framework: 'go', comparable: true, staticTitle: true,
    context: 'same-context', body: { exact: 'body', structural: 'shape' } };
  const dir = tmp({ 'ir.json': JSON.stringify({ version: 1, tests: [unit, { ...unit, line: 8, title: 'B', fullTitle: 'B' }] }) });
  const result = await cli(['code', '--ir', path.join(dir, 'ir.json'), '-f', 'json']);
  assert.equal(result.code, 0, result.err);
  assert.ok(JSON.parse(result.out).findings.some((f) => f.code === 'K01'));
});

test('コードCLI: 未対応言語・構文エラー・0件・入力への上書きは成功にしない', async () => {
  const dir = tmp({ 'a.test.ts': 'test(', 'sample.go': 'package sample', 'plain.js': 'const x = 1;' });
  for (const file of ['a.test.ts', 'sample.go', 'plain.js']) assert.equal((await cli(['code', path.join(dir, file)])).code, 2);
  assert.equal((await cli(['code', path.join(dir, 'a.test.ts'), '-o', path.join(dir, 'a.test.ts')])).code, 2);
});
