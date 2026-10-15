'use strict';
// テストケースとしての妥当性（O / X / M・C05・U05）
const test = require('node:test');
const assert = require('node:assert/strict');
const { run, byCode } = require('./helpers');
const { suggestRows } = require('../src/validity');

test('判定: 見て確かめられない・弱い・エラーだけ・副作用を見ていない', async () => {
  const f = await run(`
- id: A
  title: 保存
  preconditions: [編集画面を開いている]
  steps: [保存ボタンを押す]
  expected: [データベースに保存される]
- id: B
  title: 遷移
  preconditions: [ログイン画面を開いている]
  steps: [ログインボタンを押す]
  expected: [ホーム画面が表示される]
- id: C
  title: 空で保存
  preconditions: [編集画面を開いている]
  steps: [名前を空欄のまま保存ボタンを押す]
  expected: [エラーが表示される]
`);
  assert.deepEqual(byCode(f, 'O01').map((x) => x.cases[0]), ['A']);
  assert.deepEqual(byCode(f, 'O02').map((x) => x.cases[0]), ['B']);
  assert.deepEqual(byCode(f, 'O04').map((x) => x.cases[0]), ['C']);
  assert.deepEqual(byCode(f, 'O03').map((x) => x.cases[0]), ['C']);
});

test('判定: 具体的な値・変わらないことを書いたケースには言わない', async () => {
  const f = await run(`
- id: A
  title: 遷移
  preconditions: [ログイン画面を開いている]
  steps: [ログインボタンを押す]
  expected: [ホーム画面に「ようこそ」と表示される]
- id: C
  title: 空で保存
  preconditions: [編集画面を開いている]
  steps: [名前を空欄のまま保存ボタンを押す]
  expected: [「名前を入力してください」と表示される, 一覧の件数が変わらない]
`);
  assert.deepEqual(['O01', 'O02', 'O03', 'O04'].flatMap((c) => byCode(f, c)), []);
});

test('実行: 用意していないデータ・ログイン・始める画面', async () => {
  const f = await run(`
- id: A
  title: 注文を取り消す
  preconditions: [注文一覧画面を開いている]
  steps: [注文 O-300 を取り消す]
  expected: [「取り消しました」と表示される]
- id: B
  title: 用意してある
  preconditions: [注文 O-300 がある, 注文一覧画面を開いている, ログイン済み]
  steps: [注文 O-300 を取り消す]
  expected: [「取り消しました」と表示される]
- id: C
  title: マイページ
  steps: [マイページを開く]
  expected: [「会員情報」と表示される]
- id: D
  title: 始め
  steps: [保存ボタンを押す]
  expected: [「保存しました」と表示される]
`);
  assert.deepEqual(byCode(f, 'X01').map((x) => x.cases[0]), ['A']);
  assert.match(byCode(f, 'X01')[0].message, /O-300/);
  assert.deepEqual(byCode(f, 'X02').map((x) => x.cases[0]), ['C']);
  assert.deepEqual(byCode(f, 'X03').map((x) => x.cases[0]), ['D']);
});

test('条件の組: 2 つずつの組み合わせの漏れと、埋める行の提案', async () => {
  const rows = [['管理者', '受付', 'カード'], ['一般', '発送済み', 'カード'], ['一般', '受付', '振込']];
  const f = await run(rows.map(([r, s, p], i) => `- {id: P${i}, title: t${i}, conditions: {役割: ${r}, 状態: ${s}, 支払: ${p}}, preconditions: [一覧画面を開いている], steps: [取り消す], expected: ["「取り消しました」と表示される"]}`).join('\n'));
  const m01 = byCode(f, 'M01');
  assert.equal(m01.length, 1);
  assert.match(m01[0].message, /3 組/);
  const keys = ['役割', '状態', '支払'];
  const domains = new Map([['役割', new Set(['管理者', '一般'])], ['状態', new Set(['受付', '発送済み'])], ['支払', new Set(['カード', '振込'])]]);
  const extra = suggestRows(keys, domains, [[['役割', '管理者'], ['状態', '発送済み']], [['役割', '管理者'], ['支払', '振込']], [['状態', '発送済み'], ['支払', '振込']]]);
  assert.ok(extra.length <= 2, JSON.stringify(extra));
});

test('条件の組: 同じ組で結果が逆・同じ結果を重ねて確かめている・通る値しか試していない', async () => {
  const f = await run(`
- {id: A, title: a, conditions: {会員: 一般}, preconditions: [カート画面を開いている], steps: [購入ボタンを押す], expected: [購入できる]}
- {id: B, title: b, conditions: {会員: 一般}, preconditions: [カート画面を開いている], steps: [購入ボタンを押下する], expected: [購入できない]}
- {id: C, title: c, preconditions: [登録画面を開いている], steps: [名前に「山田」を入力する, 登録を押す], expected: [「登録しました」と表示される]}
- {id: D, title: d, preconditions: [登録画面を開いている], steps: [名前に「佐藤」を入力する, 登録を押す], expected: [「登録しました」と表示される]}
- {id: E, title: e, preconditions: [登録画面を開いている], steps: [名前に「佐藤」を入力して, 登録ボタンを押す], expected: [「登録しました」と表示されること]}
`);
  assert.deepEqual(byCode(f, 'C05').map((x) => x.cases), [['A', 'B']]);
  assert.ok(byCode(f, 'U05').some((x) => x.cases.includes('D') && x.cases.includes('E')), JSON.stringify(f.map((x) => [x.code, x.cases])));
  assert.ok(byCode(f, 'M02').some((x) => x.message.includes('名前')));
});
