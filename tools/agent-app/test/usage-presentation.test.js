'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const usage = require('../src/shared/usagePresentation');

test('unclassified execution destinations form one Other row without duplicating counts or tokens', () => {
  const rows = [{ group: '(なし)', runs: 2, unmeasured_runs: 2, estimated_tokens: 30 },
    { group: 'claude', runs: 246, measured_in: 1000 },
    { group: 'custom', runs: 4, measured_in: 100, measured_out: 20 }];
  const result = usage.breakdown(rows, 'agent_cli');
  assert.equal(result.length, 2);
  assert.deepEqual(result[1], { group: 'other', runs: 6, measured_in: 100, measured_out: 20, estimated_tokens: 30, unmeasured_runs: 2 });
  assert.equal(result.reduce((sum, r) => sum + r.runs, 0), 252);
  assert.equal(rows[0].group, '(なし)', 'source records remain unchanged');
});

test('workload aliases share one workflow row; model identifiers are preserved', () => {
  const rows = [{ group: 'flow', runs: 3, measured_in: 20 }, { group: 'workflow', runs: 2, measured_in: 40 },
    { group: 'project', runs: 1 }, { group: 'routine', runs: 4, measured_in: 10 }, { group: 'task', runs: 2, measured_in: 20 }];
  const result = usage.breakdown(rows, 'workload');
  assert.equal(result.length, 3);
  assert.equal(result[0].runs, 5);
  assert.equal(result[0].measured_in, 60);
  assert.equal(result[2].group, 'task');
  assert.equal(result[2].runs, 6);
  assert.equal(result[2].measured_in, 30);
  assert.deepEqual(result.map(row => usage.WORKLOAD_LABEL[row.group]), ['ワークフロー', 'プロジェクト', 'タスク']);
  assert.equal(usage.breakdown([{ group: 'custom/model', runs: 1 }], 'model')[0].group, 'custom/model');
});
