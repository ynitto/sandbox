'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const policy = require('../src/main/approval-policy');

test('閲覧は自動で進め、外部へ影響する操作と不明な操作は承認を求める', () => {
  for (const action of ['open', 'read', 'navigate', 'search', 'snapshot', 'fill-draft']) {
    assert.strictEqual(policy.classify({ action }).level, 'safe', action);
  }
  for (const action of ['submit', 'save', 'update', 'delete', 'purchase', 'publish', 'message', 'permission', 'unknown']) {
    assert.strictEqual(policy.classify({ action }).level, 'approval-required', action);
  }
});

test('承認は同じ試行・対象の一度だけに使える', () => {
  let approval = policy.createApproval({
    id: 'a1', trialId: 't1', action: 'submit', target: '申請フォーム', effect: '申請を送信します',
  });
  approval = policy.decide(approval, 'approved');
  const consumed = policy.consume(approval, { trialId: 't1', action: 'submit', target: '申請フォーム' });
  assert.strictEqual(consumed.state, 'consumed');
  assert.throws(() => policy.consume(consumed, { trialId: 't1', action: 'submit', target: '申請フォーム' }), /一度だけ/);
  assert.throws(() => policy.consume(approval, { trialId: 't2', action: 'submit', target: '申請フォーム' }), /一致しません/);
});

test('承認されていない操作や不正な回答を拒否する', () => {
  assert.strictEqual(policy.classify().action, 'unknown');
  assert.throws(() => policy.createApproval({ action: 'read' }), /必要ありません/);
  const pending = policy.createApproval();
  assert.throws(() => policy.decide(pending), /不正/);
  assert.throws(() => policy.decide(pending, 'later'), /不正/);
  assert.throws(() => policy.decide(), /回答済み/);
  assert.throws(() => policy.consume(pending, {}), /承認されていません/);
  assert.throws(() => policy.consume(), /承認されていません/);
  const rejected = policy.decide(pending, 'rejected');
  assert.throws(() => policy.decide(rejected, 'approved'), /回答済み/);
  assert.throws(() => policy.consume(rejected, {}), /承認されていません/);
  assert.throws(() => policy.consume(policy.decide(pending, 'approved')), /一致しません/);
});
