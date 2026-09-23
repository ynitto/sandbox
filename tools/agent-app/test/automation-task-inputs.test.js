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

test('実際の workflow と外部アクションから入力パラメータを読む', () => {
  const root = repository('prompts:\n  - name: 月次\n    statemachine: report\n    input:\n      month: 2026-09\n      unused: old\n');
  const directory = path.join(root, '.statemachine', 'report');
  fs.mkdirSync(path.join(directory, 'actions'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'workflow.yaml'),
    'name: レポート\ncontext:\n  fixed: 既定値\n  optional: ""\nstates:\n  analyze:\n    action_file: actions/analyze.md\n    output_key: summary\n  done:\n    action: "{{summary}} を使う"\n    terminal: true\ntransitions:\n  - from: analyze\n    to: done\n    condition: "file:actions/condition.md"\n');
  fs.writeFileSync(path.join(directory, 'actions', 'analyze.md'),
    '{{month}} の {{fixed}} と {{last_output}} と {{input}} を分析する');
  fs.writeFileSync(path.join(directory, 'actions', 'condition.md'),
    '{{context.fixed}} と {{context.extra}} を判定する');

  assert.deepStrictEqual(inputs.definitionParameters(root, 'report'), ['context.extra', 'input', 'month', 'optional']);
  const task = inputs.enrichSnapshot(root, { tasks: [
    { kind: 'statemachine', machine: 'report', parameters: ['unused'] },
  ] }).tasks[0];
  assert.deepStrictEqual(task.parameters, ['context.extra', 'input', 'month', 'optional']);
  assert.deepStrictEqual(task.parameterDefaults, { month: '2026-09' });
});

test('省略した外部アクションと条件も自動探索する', () => {
  const root = repository('prompts: []\n');
  const directory = path.join(root, '.statemachine', 'auto');
  fs.mkdirSync(path.join(directory, 'actions'), { recursive: true });
  fs.mkdirSync(path.join(directory, 'conditions'));
  fs.writeFileSync(path.join(directory, 'workflow.yaml'),
    'states:\n  start: {}\n  done:\n    terminal: true\ntransitions:\n  - from: start\n    to: done\n');
  fs.writeFileSync(path.join(directory, 'actions', 'start.md'), '{{topic}} を調べる');
  fs.writeFileSync(path.join(directory, 'conditions', 'start_to_done.md'), '{{quality}} を満たす');
  assert.deepStrictEqual(inputs.definitionParameters(root, 'auto'), ['quality', 'topic']);
});
