'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const compiler = require('../src/main/flow-teaching-compiler');

test('適応型仕様とAI候補を検査済みの小さな制御骨格へ変換する', () => {
  const result = compiler.compile({
    workflowId: 'review-flow', title: '変更レビュー',
    understanding: { purpose: '変更をレビューする', qualityCriteria: ['根拠を示す'] },
    candidate: { nodes: [
      { id: 'work', label: '調査とレビュー', kind: 'work', goal: '{{request}}。根拠を示す', deps: [] },
    ] },
  });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.workflow.id, 'review-flow');
  assert.strictEqual(result.workflow.nodes.length, 1);
  assert.strictEqual(result.digest, result.preview.digest);
});
