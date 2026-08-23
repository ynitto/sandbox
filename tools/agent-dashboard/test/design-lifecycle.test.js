'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');
const adhocPreload = require('../src/features/adhoc-flow/preload');
const design = require('../src/features/adhoc-flow/main/design-session');
const workflowUi = require('../src/renderer/features/adhoc-flow');

test('preload は設計の差し戻し理由を専用 IPC channel へ渡す', () => {
  const calls = [];
  const invoke = (...args) => { calls.push(args); return { ok: true }; };

  adhocPreload.preparationReviseDesign(invoke)({ id: 'prep-1', feedback: '検証方法を具体化してください' });

  assert.deepStrictEqual(calls, [[
    'preparation:reviseDesign', { id: 'prep-1', feedback: '検証方法を具体化してください' },
  ]]);
});

test('設計フローの表示にはユーザー承認・完了・差し戻しのDashboard工程を合成する', () => {
  const workflow = adhoc.loadWorkflow({ adhocFlow: {} }, 'design-auto', {
    scope: 'builtin', purpose: 'design',
  });
  const visual = workflowUi.visualWorkflow(workflow);
  const byId = new Map(visual.nodes.map((node) => [node.id, node]));

  assert.deepStrictEqual(byId.get('__design-user-review__').deps, ['review']);
  assert.deepStrictEqual(byId.get('__design-complete__').deps, ['__design-user-review__']);
  assert.deepStrictEqual(byId.get('__design-revise__').deps, ['__design-user-review__']);
  assert.deepStrictEqual(byId.get('__design-revise__').runtimeReturns, ['requirements']);
  assert.deepStrictEqual(visual.exit, ['__design-complete__']);
  assert.ok(['__design-user-review__', '__design-complete__', '__design-revise__']
    .every((id) => byId.get(id).runtime && byId.get(id).lifecycle));
});

test('差し戻し理由は現在の設計書と一緒に次ラウンドへ渡す', () => {
  const request = design.buildRoundRequest({
    goal: 'CSV対応を改善する',
    document: '## 目的\n既存の設計',
    feedback: '受入基準を観測可能な表現に直してください',
  });

  assert.ok(request.includes('## 現在の設計書\n## 目的\n既存の設計'));
  assert.ok(request.includes('## ユーザーからの差し戻し\n受入基準を観測可能な表現に直してください'));
});
