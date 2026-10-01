'use strict';

// 失敗した実行の結果カード: 次の操作を失敗の種類で選ぶ（CHANGELOG agent-app 0.47.0 の約束）。
//   retry（一時的） … 「続きから再実行」が主操作
//   setup（認証・環境・上限・停止の指示） … 直し方の 1 行を出し、再実行を勧めない
//   content（工程の中身） … 初回は再実行、同じ失敗が続いたら「依頼を直して実行」「手順を直す」
// 描くのは本物の renderer（vm で読み込む）、直し方の文言は main の nodeErrorOf が決める。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/renderer/automation/flow.js'), 'utf8');

function failedRun({ cls, group, remedy = '', attempts = [], workflowId = 'sample' }) {
  return {
    runId: 'run-1', title: '依頼', request: '依頼', workflowId, state: 'failed', terminal: true,
    createdAt: '2026-10-01T00:00:00Z', readonly: false, revision: 1, attempts,
    progress: { done: 1, failed: 1, total: 2 }, interactions: [], teamwork: null, delivery: null,
    nodes: [
      { id: 'make', label: '作る', kind: 'work', state: 'done', output: 'できました' },
      { id: 'check', label: '確かめる', kind: 'verify', state: 'failed', output: '理由の 1 行',
        error: { cls, group, message: '理由の 1 行', remedy } },
    ],
  };
}

async function render(run, history = []) {
  const window = { Publish: { badgeHtml: () => '', cardHtml: () => '' } };
  vm.runInNewContext(source, { window, document: { getElementById: () => null }, clearTimeout, setTimeout: () => 0 });
  const runs = [run, ...history];
  const ctx = {
    name: 'ワークフロー', root: () => '/repo', config: () => ({ execution: { tiers: {} } }),
    agents: () => ['codex'], isActive: () => false, refresh: () => {},
    guard: async (_label, action) => action(), toast: () => {}, escape: String,
    dateLabel: () => '', changed: () => {}, teachView: () => {},
    bridge: {
      catalog: async () => ({ kinds: [], patterns: [] }), list: async () => [], read: async () => null,
      context: async () => ({ agents: ['codex'], defaults: {}, tools: { agentFlow: { ok: true } }, workspace: { ok: true } }),
      runList: async () => runs, runRead: async () => run, runLog: async () => null,
      teachingList: async () => [],
    },
  };
  const feature = window.createFlowFeature(ctx);
  await feature.activate();
  const clicks = {};
  const button = { dataset: { flowRun: run.runId }, addEventListener: (_n, fn) => { clicks.run = fn; } };
  feature.bind({ querySelector: () => null, querySelectorAll: (sel) => (sel === '[data-flow-run]' ? [button] : []) });
  await clicks.run();
  const html = feature.html();
  return html.slice(html.indexOf('flow-outcome'), html.indexOf('</section>', html.indexOf('flow-outcome')));
}

const primaryOf = (card) => (/<button type="button" class="primary"[^>]*>([^<]+)</.exec(card) || [])[1] || null;

for (const [cls, remedy] of [
  ['auth', 'AI にログインし直してから再実行してください'],
  ['env', '必要なコマンドが入っているか、接続先に届くかを確かめてから再実行してください'],
  ['quota', '利用上限・レート制限を見直すか、解除されてから再実行してください'],
  ['control', '実行を止める指示（一時停止・停止）を解除してから再実行してください'],
]) {
  test(`setup（${cls}）の失敗は直し方を出し、再実行を主操作にしない`, async () => {
    const card = await render(failedRun({ cls, group: 'setup', remedy }));
    assert.ok(card.includes(remedy), '直し方の 1 行を出す');
    assert.strictEqual(primaryOf(card), null, '主操作を置かない（再実行が先頭の強い操作にならない）');
    assert.doesNotMatch(card, />続きから再実行</, '「今すぐ繰り返せば直る」ように見せない');
    assert.match(card, /class="ghost" data-flow-resume>直したので続きから再実行</, '直したあとに戻る道は控えめに残す');
    assert.ok(card.indexOf('data-flow-log') < card.indexOf('data-flow-resume'), '再実行より先にログを置く');
  });
}

test('setup の失敗が続いても「依頼を直して実行」を勧めない（依頼の中身の問題ではない）', async () => {
  const card = await render(failedRun({ cls: 'auth', group: 'setup', remedy: 'AI にログインし直してから再実行してください',
    attempts: [{ nodeId: 'check', cls: 'auth' }] }));
  assert.strictEqual(primaryOf(card), null);
  assert.doesNotMatch(card, /data-flow-rerun|data-flow-edit-steps/);
});

test('一時的な失敗は「続きから再実行」が主操作のまま', async () => {
  for (const cls of ['transient', 'integration']) {
    const card = await render(failedRun({ cls, group: 'retry', attempts: [{ nodeId: 'check', cls }] }));
    assert.strictEqual(primaryOf(card), '続きから再実行', cls);
  }
});

test('中身の初回失敗は再実行が主操作、同じ失敗が続いたら依頼と手順を直すのが先', async () => {
  const first = await render(failedRun({ cls: 'content', group: 'content' }));
  assert.strictEqual(primaryOf(first), '続きから再実行');
  assert.match(first, /data-flow-rerun>依頼を直して実行/);

  const repeated = await render(failedRun({ cls: 'content', group: 'content', attempts: [{ nodeId: 'check', cls: 'content' }] }));
  assert.strictEqual(primaryOf(repeated), '依頼を直して実行');
  assert.match(repeated, /data-flow-edit-steps>手順を直す/);
  assert.match(repeated, /class="ghost" data-flow-resume>続きから再実行/);
  assert.match(repeated, /2 回続けて同じ工程で失敗しています/);
});
