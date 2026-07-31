'use strict';

// flow.reconcileNodeState（gitlab executor のクローズ判定をビュアー側で先読みする純関数）の
// 検証。executor（executors/gitlab.py の _mr_decision / _closed_issue_decision /
// _decision_from_comments）と同じ規則で、クローズ済みイシューに紐づく非終端ノードを
// done/failed へ先読み反映することを確かめる。追加依存なしで単体実行できる。

const assert = require('assert');
const flow = require('../src/main/flow');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const claimed = { state: 'claimed' };

// --- 反映しないケース（bus が正、または未決着） ---
test('終端ノード（done/failed）は反映しない（bus が正）', () => {
  const issue = { state: 'closed', labels: ['status:done'], relatedMrs: [] };
  assert.strictEqual(flow.reconcileNodeState({ state: 'done' }, issue), null);
  assert.strictEqual(flow.reconcileNodeState({ state: 'failed' }, issue), null);
});

test('イシューが open のうちは反映しない（executor が open で result を書く）', () => {
  const issue = { state: 'opened', labels: ['status:approved'], relatedMrs: [] };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), null);
});

test('イシュー未取得（null）は反映しない', () => {
  assert.strictEqual(flow.reconcileNodeState(claimed, null), null);
});

// --- 承認（done）ケース ---
test('クローズ＋ status:done ラベル → done', () => {
  const issue = { state: 'closed', labels: ['status:done'], relatedMrs: [] };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'done');
});

test('クローズ＋ status:approved ラベル → done', () => {
  const issue = { state: 'closed', labels: ['status:approved'], relatedMrs: [] };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'done');
});

test('クローズ＋関連 MR がすべて merged → done（ラベル無しでも MR で決着）', () => {
  const issue = {
    state: 'closed',
    labels: [],
    relatedMrs: [{ state: 'merged' }, { state: 'merged' }],
  };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'done');
});

test('クローズ＋人コメントが承認語 → done（MR/ラベル無し・review-viewer の MR なし承認）', () => {
  const issue = {
    state: 'closed',
    labels: [],
    relatedMrs: [],
    comments: [{ system: false, body: '承認します。ありがとう。' }],
  };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'done');
});

// --- 却下（failed）ケース ---
test('クローズ＋未マージ closed の関連 MR → failed', () => {
  const issue = {
    state: 'closed',
    labels: [],
    relatedMrs: [{ state: 'closed' }],
  };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'failed');
});

test('クローズ＋人コメントが却下語 → failed（却下語は承認語より優先）', () => {
  const issue = {
    state: 'closed',
    labels: [],
    relatedMrs: [],
    comments: [{ system: false, body: 'これは却下します。承認できません。' }],
  };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'failed');
});

test('MR 無し・ラベル無し・手掛かり無しのクローズ → failed（取り下げ扱い）', () => {
  const issue = { state: 'closed', labels: [], relatedMrs: [], comments: [] };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'failed');
});

test('agent-flow 自身の自動コメント/system note は手掛かりにしない', () => {
  const issue = {
    state: 'closed',
    labels: [],
    relatedMrs: [],
    // system note と agent-flow: 自動コメントだけ → 手掛かり無し → 取り下げ＝failed
    comments: [
      { system: true, body: '承認しました' },
      { system: false, body: 'agent-flow:task-token:kf-abc 承認' },
    ],
  };
  assert.strictEqual(flow.reconcileNodeState(claimed, issue), 'failed');
});

// --- 補助関数の直接検証（executor の _mr_decision と一致） ---
test('gitlabMrDecision: open な MR があれば未決着', () => {
  assert.strictEqual(flow.gitlabMrDecision(['opened', 'merged']), '');
});
test('gitlabMrDecision: 未マージ closed があれば rejected', () => {
  assert.strictEqual(flow.gitlabMrDecision(['merged', 'closed']), 'rejected');
});
test('gitlabMrDecision: すべて merged なら approved', () => {
  assert.strictEqual(flow.gitlabMrDecision(['merged', 'merged']), 'approved');
});
test('gitlabMrDecision: MR 無しは未決着', () => {
  assert.strictEqual(flow.gitlabMrDecision([]), '');
});

console.log(`\n${passed} passed`);
