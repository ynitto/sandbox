'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const teaching = require('../src/main/flow-teaching-model');

test('目的から候補を試運転し、利用者承認後だけ利用可能にする', () => {
  let session = teaching.createSession({ workflowId: 'review-flow', purpose: '変更を柔軟にレビューする' });
  assert.strictEqual(session.status, 'draft');
  session = teaching.addGeneration(session, {
    id: 'g1', summary: '調査、レビュー、統合を行う',
    workflowSpec: { purpose: '変更を柔軟にレビューする', qualityCriteria: ['根拠がある'] },
    workflow: { version: 2, id: 'review-flow', name: 'レビュー', nodes: [{ id: 'work', goal: '確認', kind: 'work', deps: [] }] },
    digest: 'abc123',
  });
  assert.strictEqual(session.status, 'needs-trial');
  session = teaching.recordTrial(session, { id: 't1', generationId: 'g1', runId: 'run-1', outcome: 'passed', assessment: { met: ['根拠がある'] } });
  assert.strictEqual(session.status, 'awaiting-confirmation');
  session = teaching.confirmReady(session, 'g1', 'abc123');
  assert.strictEqual(session.status, 'ready');
  assert.strictEqual(session.lastSuccessfulGenerationId, 'g1');
});
