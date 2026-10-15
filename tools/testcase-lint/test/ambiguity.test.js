'use strict';
// テストとして曖昧な点（A01〜A09）
const test = require('node:test');
const assert = require('node:assert/strict');
const { run, byCode } = require('./helpers');

const A = (f) => f.filter((x) => x.code.startsWith('A')).map((x) => `${x.code} ${x.message.match(/「(.*?)」/)[1]}`).sort();

test('期待結果の曖昧さ: 合否の基準・選択肢・言い切らない・比べる基準が無い', async () => {
  const f = await run(`
- id: A
  title: t
  preconditions: [一覧画面を開いている]
  steps: [検索ボタンを押す]
  expected:
    - 結果が正しく表示される
    - 一覧かエラーのいずれかが表示される
    - 検索結果が表示されることがある
    - 以前より速く表示される
`);
  assert.deepEqual(A(f), ['A01 正しく', 'A05 いずれか', 'A06 ことがある', 'A07 以前より速']);
});

test('前提・手順の曖昧さ: 値・量・指すもの・操作の対象・閉じていない列挙', async () => {
  const f = await run(`
- id: A
  title: t
  preconditions:
    - 注文が複数ある
    - 該当の利用者でログイン済み
  steps:
    - 注文一覧画面を開く
    - 正しいパスワードを入力する
    - 各項目を入力する
    - 名前・住所などを入力して保存する
    - しばらく待つ
  expected: [「保存しました」と表示される]
`);
  assert.deepEqual(A(f), ['A02 正しいパスワード', 'A03 しばらく', 'A03 複数', 'A04 該当の', 'A08 各項目を', 'A09 など']);
});

test('値・数値を書いてあれば曖昧と言わない。文言の中の語も拾わない', async () => {
  const f = await run(`
- id: A
  title: t
  preconditions: [注文が複数（3 件）ある, ログイン画面を開いている]
  steps:
    - 正しいパスワード「Passw0rd」を入力する
    - 項目「氏名」に「山田」を入力する
    - ログインボタンを押す
  expected:
    - 「いずれかを選んでください」と表示される
    - 3 秒以内に、前より速く表示される
    - 合計が小計と等しい
`);
  assert.deepEqual(A(f), []);
});

test('最初の文が指示語で始まる', async () => {
  const f = await run(`
- id: A
  title: t
  steps: [その注文を取り消す]
  expected: [「取り消しました」と表示される]
`);
  assert.deepEqual(A(f), ['A04 その']);
});

test('チェックリストの項目そのものに当てる', async () => {
  const f = await run('{"checklist": ["レイアウトが崩れていないこと", "エラー時に適切なメッセージが表示されること"]}', { name: 'c.json' });
  assert.deepEqual(A(f), ['A01 崩れていない', 'A01 適切な']);
});

test('設定で合否の基準があいまいな語を足せる', async () => {
  const f = await run('- {id: A, title: t, preconditions: [画面を開いている], steps: [押す], expected: [見栄えがよい]}\n', { config: { words: { vague: ['見栄えがよい'], negative: [], pairs: [] } } });
  assert.deepEqual(byCode(f, 'A01').length, 1);
});
