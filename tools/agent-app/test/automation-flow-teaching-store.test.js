'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const store = require('../src/main/automation/flow-teaching-store');

test('教示下書きを標準workflow一覧に混ぜず保存して再開する', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'flow-teaching-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const created = store.create(root, { workflowId: 'review-flow', purpose: '変更をレビューする' });
  assert.strictEqual(created.status, 'draft');
  assert.deepStrictEqual(fs.readdirSync(path.join(root, '.agents', 'workflows')).sort(), ['.teaching']);
  assert.strictEqual(store.load(root, 'review-flow').understanding.purpose, '変更をレビューする');
  assert.deepStrictEqual(store.list(root).map((item) => item.workflowId), ['review-flow']);
});
