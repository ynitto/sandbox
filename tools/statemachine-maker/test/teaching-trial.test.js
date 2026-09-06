'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const teaching = require('../src/main/teaching-model');
const teachingStore = require('../src/main/teaching-store');
const trial = require('../src/main/teaching-trial');
const store = require('../src/main/store');

const SPEC = {
  name: '確認', machine: 'report', purpose: '確認する',
  steps: [{ kind: 'agent', title: '確認する', detail: '内容を確認する' }],
};

test('候補を元定義と分離した一時ワークフローへ置き、試運転後に片付ける', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-trial-'));
  let session = teaching.createSession({ machine: 'report', purpose: '確認する' });
  session = teaching.addGeneration(session, { id: 'g1', jobSpec: { purpose: '確認する' }, makerSpec: SPEC });
  teachingStore.save(root, 'report', session);

  const staged = trial.stage(root, 'report', 'g1', { id: 'abc123' });
  assert.strictEqual(staged.trialMachine, 'trial-abc123');
  assert.ok(fs.existsSync(path.join(root, '.statemachine', 'trial-abc123', 'workflow.yaml')));
  assert.deepStrictEqual(store.list(root), [], '一時ワークフローを通常一覧へ出さない');
  assert.ok(!fs.existsSync(path.join(root, '.statemachine', 'report', 'workflow.yaml')), '正式版を試運転で上書きしない');

  trial.cleanup(root, staged.trialMachine);
  assert.ok(!fs.existsSync(path.join(root, '.statemachine', staged.trialMachine)));
  assert.throws(() => trial.cleanup(root, 'report'), /一時ワークフロー/);
});

test('有効な候補以外は試運転へ置かない', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-trial-errors-'));
  teachingStore.save(root, 'empty', teaching.createSession({ machine: 'empty' }));
  assert.throws(() => trial.stage(root, 'empty'), /候補/);

  let session = teaching.createSession({ machine: 'report', title: '確認' });
  session = teaching.addGeneration(session, { id: 'g1', makerSpec: SPEC });
  teachingStore.save(root, 'report', session);
  assert.throws(() => trial.stage(root, 'report', '', { id: '$$$' }), /識別子/);
  const staged = trial.stage(root, 'report');
  assert.match(staged.trialMachine, /^trial-/);
  assert.deepStrictEqual(staged.jobSpec, {});
  trial.cleanup(root, staged.trialMachine);
  assert.deepStrictEqual(trial.cleanup(root, 'trial-does-not-exist'), { removed: 'trial-does-not-exist' });
});
