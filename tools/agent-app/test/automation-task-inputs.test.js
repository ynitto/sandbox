'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const inputs = require('../src/main/automation/task-inputs');

function repository(yaml) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-inputs-'));
  fs.mkdirSync(path.join(root, '.agents'));
  fs.writeFileSync(path.join(root, '.agents', 'agent-loop.yaml'), yaml);
  return root;
}

test('ステートマシンを起動する entry はタスクへ統合し、既定入力を引き継ぐ', () => {
  const root = repository('prompts:\n  - name: 日次\n    statemachine: report\n    input:\n      date: today\n      owner: ""\n');
  const snapshot = inputs.enrichSnapshot(root, { tasks: [
    { id: 'machine:report', kind: 'statemachine', machine: 'report', parameters: ['format'] },
    { id: 'prompt:daily', kind: 'prompt', entry: { statemachine: 'report' } },
  ] });
  assert.strictEqual(snapshot.tasks.length, 1);
  assert.deepStrictEqual(snapshot.tasks[0].parameters, ['format', 'date', 'owner']);
  assert.deepStrictEqual(snapshot.tasks[0].parameterDefaults, { date: 'today', owner: '' });
  assert.deepStrictEqual(inputs.requiredInput(snapshot.tasks[0], { format: 'md' }).missing, ['owner']);
});

test('識別名変更時は agent-loop の参照も変更する', () => {
  const root = repository('prompts:\n  - name: 日次\n    statemachine: report\n');
  assert.strictEqual(inputs.renameReferences(root, 'report', 'daily-report'), 1);
  assert.match(fs.readFileSync(path.join(root, '.agents', 'agent-loop.yaml'), 'utf8'), /statemachine: daily-report/);
});
