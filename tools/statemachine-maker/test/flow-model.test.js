'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const flow = require('../src/main/flow-model');

function workflow(patch = {}) {
  return {
    version: 2, id: 'review-flow', name: '変更をレビュー', description: '', purpose: 'implementation',
    entry: ['draft'], exit: ['check'],
    nodes: [
      { id: 'draft', label: '案を作る', kind: 'generate', goal: '{{request}} を {{audience}} 向けに整理する', deps: [], tier: 'auto', x: 10, y: 20 },
      { id: 'check', label: '確認する', kind: 'human', goal: '案を確認する', deps: ['draft'], tier: 'auto', interaction: { mode: 'approval', prompt: 'この案で進めますか？' } },
    ],
    ...patch,
  };
}

test('保存形を正規化し、入力を埋めた agent-flow plan を作る', () => {
  const result = flow.preview(workflow(), '設計を作る', { audience: '利用者' });
  assert.strictEqual(result.ok, true);
  assert.deepStrictEqual(result.parameterKeys, ['audience']);
  assert.strictEqual(result.plan.nodes[0].goal, '{{request}} を 利用者 向けに整理する');
  assert.deepStrictEqual(result.workflow.entry, ['draft']);
  assert.deepStrictEqual(result.workflow.exit, ['check']);
  assert.strictEqual(result.workflow.nodes[0].tier, 'auto');
  assert.ok(!Object.prototype.hasOwnProperty.call(result.workflow.nodes[1], 'tier'));
});

test('循環・未知の接続・split の後続・human の確認不足を issues で返す', () => {
  const result = flow.preview(workflow({ nodes: [
    { id: 'a', kind: 'split', goal: '分ける', deps: ['b'] },
    { id: 'b', kind: 'work', goal: '', deps: ['a', 'missing'] },
    { id: 'human', kind: 'human', goal: '確認', deps: [] },
  ] }));
  const codes = new Set(result.issues.map((item) => item.code));
  for (const code of ['goal-required', 'dep-unknown', 'dep-on-split', 'dep-cycle', 'interaction-required']) assert.ok(codes.has(code), code);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.workflow, null);
  assert.ok(result.draft);
});

test('digest は表示位置と日時だけの変更では変わらない', () => {
  const one = flow.preview(workflow()).digest;
  const moved = workflow({ createdAt: '2000-01-01', updatedAt: '2030-01-01' });
  moved.nodes = moved.nodes.map((node) => ({ ...node, x: 999, y: -20 }));
  assert.strictEqual(flow.preview(moved).digest, one);
  moved.nodes[0].goal = '別の作業';
  assert.notStrictEqual(flow.preview(moved).digest, one);
});

test('予約済みの実行変数と未入力パラメータを明示する', () => {
  const raw = workflow();
  raw.nodes[0].goal = '{{today}} に {{target}} を調べる';
  const result = flow.preview(raw, '依頼', {});
  assert.ok(result.issues.some((item) => item.code === 'parameter-reserved'));
  assert.ok(result.issues.some((item) => item.code === 'goal-has-unfilled-parameter' && item.message.includes('target')));
});
