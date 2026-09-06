'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

test('利用者の依頼だけを同じリポジトリのタスク教示intentへ変換する', () => {
  const { create } = require('../src/renderer/taskIntent');
  const intent = create({
    id: 'intent-1',
    root: '/repo/a',
    message: { role: 'user', text: '毎月このレポートを作って', attachments: [{ id: 'secret-path', name: '売上.csv', size: 42 }] },
    execution: { agent: 'codex', model: 'gpt-test' },
  });
  assert.deepStrictEqual(intent, {
    version: 1,
    id: 'intent-1',
    root: '/repo/a',
    purpose: '毎月このレポートを作って',
    attachments: [{ name: '売上.csv', size: 42 }],
    agent: 'codex',
    model: 'gpt-test',
  });
});

test('同じintentは対象リポジトリで一度だけ消費する', () => {
  const { consume } = require('../src/renderer/taskIntent');
  const intent = { version: 1, id: 'intent-1', root: '/repo/a', purpose: '月次レポートを作る' };
  assert.deepStrictEqual(consume(intent, { root: '/repo/a', consumedId: '' }), {
    accepted: true, intent, consumedId: 'intent-1',
  });
  assert.deepStrictEqual(consume(intent, { root: '/repo/a', consumedId: 'intent-1' }), {
    accepted: false, reason: 'consumed', consumedId: 'intent-1',
  });
  assert.deepStrictEqual(consume(intent, { root: '/repo/b', consumedId: '' }), {
    accepted: false, reason: 'root', consumedId: '',
  });
});

test('教示中の下書きを実行可能なタスクと重複せず一覧へ加える', () => {
  const { taskItems } = require('../src/renderer/navigation');
  const items = taskItems(
    { tasks: [{ id: 'machine:ready', machine: 'ready', name: '完成済み' }] },
    [],
    [
      { machine: 'draft', title: '作成中', status: 'draft' },
      { machine: 'ready', title: '完成済み', status: 'ready' },
    ],
  );
  assert.deepStrictEqual(items, [
    { id: 'machine:ready', machine: 'ready', name: '完成済み' },
    { id: 'machine:draft', machine: 'draft', name: '作成中', teachingStatus: 'draft', teaching: true },
  ]);
});

test('定義があるタスクはAIとの変更が進んでいても実行できる項目のまま、変更中の印だけを添える', () => {
  const { taskItems } = require('../src/renderer/navigation');
  const tasks = [
    { id: 'machine:report', machine: 'report', name: '月次レポート' },
    { id: 'machine:check', machine: 'check', name: 'リリース確認' },
    { id: 'entry:abc', kind: 'prompt', name: '定期レビュー' },
  ];
  const items = taskItems({ tasks }, [], [
    { machine: 'report', title: '月次レポート', status: 'needs-trial', published: true },
    { machine: 'check', title: 'リリース確認', status: 'ready', published: true },
  ]);
  assert.deepStrictEqual(items, [
    { id: 'machine:report', machine: 'report', name: '月次レポート', change: 'needs-trial' },
    { id: 'machine:check', machine: 'check', name: 'リリース確認' },
    { id: 'entry:abc', kind: 'prompt', name: '定期レビュー' },
  ]);
  assert.ok(!items.some((item) => item.teaching), '定義があるものを教示中の下書きとして重複させない');
});

test('リポジトリがない依頼をタスク化しない', () => {
  const { create } = require('../src/renderer/taskIntent');
  assert.throws(() => create(), /リポジトリ/);
  assert.throws(() => create('invalid'), /リポジトリ/);
  assert.throws(() => create({ message: { role: 'user', text: '依頼' } }), /リポジトリ/);
});

test('利用者以外の発言や空の発言をタスク化しない', () => {
  const { create } = require('../src/renderer/taskIntent');
  assert.throws(() => create({ root: '/repo', message: { role: 'assistant', text: '回答' } }), /依頼/);
  assert.throws(() => create({ root: '/repo', message: { role: 'user' } }), /依頼/);
  assert.throws(() => create({ root: '/repo', message: { role: 'user', text: '  ' } }), /依頼/);
  assert.throws(() => create({ root: '/repo', message: null }), /依頼/);
  assert.throws(() => create({ root: '/repo', message: 'invalid' }), /依頼/);
});

test('空の実行設定と添付を安全な既定値へ丸める', () => {
  const { create } = require('../src/renderer/taskIntent');
  assert.deepStrictEqual(create({
    root: '/repo', id: null, message: { role: 'user', text: '依頼', attachments: [null, {}, { name: 'a', size: null }] }, execution: 'invalid',
  }), {
    version: 1, id: '', root: '/repo', purpose: '依頼', attachments: [{ name: 'a', size: 0 }], agent: '', model: '',
  });
  assert.deepStrictEqual(create({ root: '/repo', message: { role: 'user', text: '依頼' } }).attachments, []);
});

test('不正な消費状態とintentを拒否する', () => {
  const { consume } = require('../src/renderer/taskIntent');
  assert.deepStrictEqual(consume(), { accepted: false, reason: 'root', consumedId: '' });
  assert.deepStrictEqual(consume({ id: '', root: '/repo' }, null), { accepted: false, reason: 'root', consumedId: '' });
  assert.deepStrictEqual(consume({ id: '', root: '/repo' }, 'invalid'), { accepted: false, reason: 'root', consumedId: '' });
});

test('ブラウザではintent契約をwindowへ公開する', () => {
  const modulePath = require.resolve('../src/renderer/taskIntent');
  const originalWindow = global.window;
  try {
    global.window = {};
    delete require.cache[modulePath];
    require(modulePath);
    assert.strictEqual(typeof global.window.TaskIntent.create, 'function');
  } finally {
    if (originalWindow === undefined) delete global.window;
    else global.window = originalWindow;
    delete require.cache[modulePath];
    require(modulePath);
  }
});
