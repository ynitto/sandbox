'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { FieldMap, loadFile, stripFences } = require('../src/load');
const { tmp } = require('./helpers');

function load(content, name = 'cases.yaml') {
  const dir = tmp({ [name]: content });
  return loadFile(path.join(dir, name), name, new FieldMap());
}

test('日本語の項目名と行番号', () => {
  const { cases } = load(`
テストケース:
  - テストID: T-1
    テスト項目: ログイン
    前提条件: 登録済み
    手順: |
      1. 画面を開く
      2. ログインする
    期待結果: ホーム画面が表示される
`);
  assert.equal(cases.length, 1);
  const c = cases[0];
  assert.deepEqual([c.id, c.title, c.line], ['T-1', 'ログイン', 2]);
  assert.deepEqual(c.steps.map((i) => i.text), ['画面を開く', 'ログインする']);
  assert.deepEqual(c.steps.map((i) => i.line), [6, 7]);
  assert.equal(c.expected[0].text, 'ホーム画面が表示される');
});

test('Gherkin の given / when / then', () => {
  const c = load(`
scenarios:
  - name: 検索
    given: 商品が 3 件ある
    when: [検索欄に「りんご」と入力する, 検索ボタンを押す]
    then: りんごの商品が 1 件表示される
`).cases[0];
  assert.deepEqual([c.preconditions.length, c.steps.length, c.expected.length], [1, 2, 1]);
});

test('手順の expect: は期待結果に、隣の variants はケースにしない、知らない項目は前提に', () => {
  const { cases } = load(`
suite: s
variants:
  - { name: ja, locale: ja-JP }
cases:
  - id: TC-1
    title: t
    mocks: [{ url: "**/api", status: 500 }]
    steps:
      - goto: /
      - click: { role: button, name: 保存 }
      - expect: { target: { role: status }, text: 保存しました }
`);
  assert.equal(cases.length, 1);
  const c = cases[0];
  assert.equal(c.steps.length, 2);
  assert.match(c.expected[0].text, /保存しました/);
  assert.match(c.expected[0].field, /^steps\[2\]\./);
  assert.ok(c.preconditions.some((i) => i.text.includes('mocks')));
});

test('手順の { action, expected } と conditions', () => {
  const c = load(`
- id: A
  title: t
  conditions: { 役割: 管理者, 件数: 3 }
  steps:
    - { action: 保存ボタンを押す, expected: 保存しました と表示される }
`).cases[0];
  assert.equal(c.steps[0].text, '保存ボタンを押す');
  assert.equal(c.expected[0].text, '保存しました と表示される');
  assert.deepEqual(c.conditions, { 役割: '管理者', 件数: '3' });
});

test('文字列だけの列はチェックリスト', () => {
  const { cases } = load('{"checklist": ["文言が仕様書と一致すること", "ボタンが 2 つあること"]}', 'c.json');
  assert.deepEqual(cases.map((c) => c.kind), ['check', 'check']);
});

test('``` の囲みは行番号を保って読む', () => {
  const text = '説明です\n\n```yaml\n- id: A\n  title: t\n  steps: [x]\n  expected: [y]\n```\n';
  assert.equal(stripFences(text).split('\n')[3], '- id: A');
  assert.equal(load(text, 'out.md').cases[0].line, 4);
});

test('重複キーと読めないファイル', () => {
  let r = load('[{"id": "A", "title": "t", "id": "B"}]', 'c.json');
  assert.equal(r.findings.filter((f) => f.code === 'D15').length, 1);
  assert.equal(r.cases[0].id, 'B');
  r = load('cases:\n  - id: [unclosed\n');
  assert.deepEqual(r.findings.map((f) => f.code), ['D00']);
  assert.ok(r.findings[0].line >= 2);
});

test('ケースを 1 件も読み取れないファイルは D17 を出し、何も点検しないまま通さない', () => {
  for (const content of ['', 'foo: bar\n']) {
    const { cases, findings } = load(content);
    assert.equal(cases.length, 0);
    assert.deepEqual(findings.map((f) => f.code), ['D17']);
  }
  assert.deepEqual(load('cases:\n  - id: A\n    steps: [x]\n    expected: y\n').findings, []);
});
