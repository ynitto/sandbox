'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const store = require('../src/main/flow-store');

function raw(id = 'simple') {
  return { version: 2, id, name: '簡単なフロー', description: '説明', purpose: 'implementation', entry: ['work'], exit: ['work'], nodes: [{ id: 'work', label: '実行', kind: 'work', goal: '{{request}}', deps: [], tier: 'auto' }] };
}

test('作成・一覧・読み戻し・更新・削除を root 内で往復する', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-flow-store-'));
  const created = store.save(root, raw(), 'create');
  assert.strictEqual(created.saved, true);
  assert.strictEqual(created.file, '.agents/workflows/simple.json');
  assert.deepStrictEqual(store.list(root).map((item) => item.id), ['simple']);
  assert.strictEqual(store.read(root, 'simple').workflow.name, '簡単なフロー');
  assert.throws(() => store.save(root, raw(), 'create'), (err) => err.code === 'flow-exists');
  const changed = raw(); changed.name = '更新後';
  assert.strictEqual(store.save(root, changed, 'update').workflow.name, '更新後');
  assert.deepStrictEqual(store.remove(root, 'simple'), { deleted: true });
  assert.throws(() => store.read(root, 'simple'), (err) => err.code === 'flow-not-found');
});

test('検証エラーでは書かず、壊れた JSON も一覧から削除導線を失わない', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-flow-invalid-'));
  const invalid = raw('bad'); invalid.name = '';
  const result = store.save(root, invalid, 'create');
  assert.strictEqual(result.saved, false);
  assert.ok(!fs.existsSync(path.join(root, '.agents', 'workflows', 'bad.json')));
  fs.mkdirSync(store.dirOf(root), { recursive: true });
  fs.writeFileSync(path.join(store.dirOf(root), 'broken.json'), '{');
  assert.strictEqual(store.list(root)[0].name, '(読めません) broken');
  assert.throws(() => store.fileOf(root, '../outside'), (err) => err.code === 'flow-not-found');
});
