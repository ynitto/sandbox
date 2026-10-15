'use strict';
// 書き方・重複・矛盾・漏れ（D / U / C / G）
const test = require('node:test');
const assert = require('node:assert/strict');
const { run, codes, byCode } = require('./helpers');

const D = (fs) => codes(fs).filter((c) => c.startsWith('D'));

test('欠けている項目', async () => {
  const f = await run(`
- id: A
  steps: [押す]
- id: B
  title: t
  expected: [出る]
`);
  assert.deepEqual(D(f), ['D03', 'D04', 'D05']);
});

test('ID の欠けと重複（ファイルの中だけ）', async () => {
  const f = await run(`
- {id: A, title: t1, steps: [a], expected: [エラーが出る]}
- {id: A, title: t2, steps: [b], expected: [c]}
- {title: t3, steps: [d], expected: [e]}
`);
  assert.equal(byCode(f, 'D02').length, 1);
  assert.equal(byCode(f, 'D01').length, 1);
});

test('言葉の不備', async () => {
  const f = await run(`
- id: A
  title: t
  steps:
    - 任意のユーザー名を入力する
    - 前のケースと同じ手順で保存する
    - 一覧が表示されること
    - 画面を開いて、名前を入力して、日付を選んで、保存を押して、閉じる
  expected:
    - エラーメッセージが正しく表示される
    - 保存ボタンをクリックする
    - TBD
`);
  assert.deepEqual(D(f), ['D07', 'D09', 'D10', 'D11', 'D12']);
});

test('きちんと書いたケースには不備を言わない', async () => {
  const f = await run(`
- id: A
  title: 8 文字のパスワードで登録できる
  steps: [パスワード欄に「abcdefgh」を入力する, 登録ボタンを押す]
  expected: [「登録しました」と表示される]
- id: B
  title: 7 文字のパスワードは登録できない
  steps: [パスワード欄に「abcdefg」を入力する, 登録ボタンを押す]
  expected: [「8 文字以上で入力してください」と表示される]
`);
  assert.deepEqual(D(f), []);
  assert.deepEqual(codes(f).filter((c) => c.startsWith('A')), []);
});

const PAIR = `
- id: A
  title: ログインできる
  steps: [ログイン画面を開く, メールに「u@example.com」を入力する, ログインボタンを押す]
  expected: [EXP_A]
- id: B
  title: TITLE_B
  steps: [ログイン画面を開く, メールに「u@example.com」を入力する, ログインボタンを押す]
  expected: [EXP_B]
`;
const pair = (a, b, title = 'ログインできる2') => run(PAIR.replace('EXP_A', a).replace('EXP_B', b).replace('TITLE_B', title));

test('完全に同じ', async () => {
  const f = await pair('ホーム画面が表示される', 'ホーム画面が表示される');
  assert.deepEqual(byCode(f, 'U01')[0].cases, ['A', 'B']);
  assert.deepEqual(byCode(f, 'U02'), []);
});

test('ほぼ同じ', async () => {
  assert.equal(byCode(await pair('ホーム画面が表示される', 'ホームの画面が表示される'), 'U02').length, 1);
});

test('期待結果が逆なら重複ではなく矛盾', async () => {
  const f = await pair('ホーム画面が表示される', 'ホーム画面が表示されない');
  assert.equal(byCode(f, 'C01').length, 1);
  assert.deepEqual(byCode(f, 'U02'), []);
});

test('同じ手順で期待結果が違う', async () => {
  const f = await pair('ホーム画面が表示される', 'アカウントがロックされたとメールが届く');
  assert.deepEqual(codes(f).filter((c) => c.startsWith('C')), ['C03']);
});

test('値が違えば比べない', async () => {
  const f = await run(`
- {id: A, title: a, steps: [パスワードに「secret」を入力する, ログインを押す], expected: [ホーム画面が表示される]}
- {id: B, title: b, steps: [パスワードに「wrong」を入力する, ログインを押す], expected: [ホーム画面が表示されない]}
`);
  assert.deepEqual(codes(f).filter((c) => /^[CU]/.test(c)), []);
});

test('値だけが違うケースのまとまり', async () => {
  const rows = [1, 2, 3, 4].map((i) => `- {id: V${i}, title: v${i}, steps: [数量に「${i}」を入力する, 計算を押す], expected: [合計が「${i * 100}」円と表示される]}`).join('\n');
  const u03 = byCode(await run(rows), 'U03');
  assert.equal(u03.length, 1);
  assert.equal(u03[0].cases.length, 4);
});

test('ケースの中の食い違いと、時間の順の確認', async () => {
  let f = await run(`
- id: A
  title: t
  preconditions: [ログイン済みである, 未ログインである]
  steps: [保存を押す]
  expected: [保存ボタンが有効になる, 保存ボタンが無効になる]
`);
  assert.equal(byCode(f, 'C02').length, 2);
  f = await run(`
cases:
  - id: A
    title: 再試行
    steps:
      - expect: { visible: { text: 読み込めませんでした } }
      - click: { role: button, name: 再試行 }
      - expect: { hidden: { text: 読み込めませんでした } }
`);
  assert.deepEqual(byCode(f, 'C02'), []);
});

test('境界値と数値の食い違い（入力した引用の長さも数える）', async () => {
  const f = await run(`
- {id: A, title: 上限, preconditions: [パスワードは最大30文字], steps: [パスワードに30文字を入力する, 保存を押す], expected: [保存できる]}
- {id: B, title: 下限, preconditions: [パスワードは8文字以上], steps: [パスワードに「abcdefg」を入力する, 保存を押す], expected: [エラーが表示される]}
- {id: C, title: 下限ちょうど, steps: [パスワードに「abcdefgh」を入力する, 保存を押す], expected: [保存できる]}
- {id: D, title: 別の上限, preconditions: [パスワードは20文字以下], steps: [パスワードに「x」を入力する], expected: [エラーが表示される]}
`);
  const g03 = byCode(f, 'G03').map((x) => x.message);
  assert.ok(g03.some((m) => m.includes('最大30文字') && m.includes('31文字')), g03.join('\n'));
  assert.ok(!g03.some((m) => m.includes('8文字以上')), g03.join('\n'));
  assert.equal(byCode(f, 'C04').length, 1);
});

test('異常系がない・対になる条件', async () => {
  let f = await run(`
feature: 検索
cases:
  - {id: A, title: a, preconditions: [ログイン済み], steps: [検索する], expected: [結果が出る]}
  - {id: B, title: b, steps: [並べ替える], expected: [順番が変わる]}
`);
  assert.match(byCode(f, 'G02')[0].message, /検索/);
  assert.ok(byCode(f, 'G04').some((x) => x.message.includes('ログインしていない')));
  f = await run(`
- {id: A, title: a, steps: [検索する], expected: [結果が出る]}
- {id: B, title: b, steps: [未入力で検索する], expected: [メッセージが出る]}
`);
  assert.deepEqual(byCode(f, 'G02'), []);
});
