'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const teaching = require('../src/main/teaching-model');
const teachingStore = require('../src/main/teaching-store');

test('教示セッションをワークフローと同じ場所へ安全に保存して再開する', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-teaching-'));
  const dir = path.join(root, '.statemachine', 'report');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'workflow.yaml'), 'version: 1\nname: Report\n', 'utf8');

  const empty = teachingStore.load(root, 'report');
  assert.strictEqual(empty.machine, 'report');
  assert.strictEqual(empty.status, 'draft');

  const session = teaching.createSession({ machine: 'report', purpose: 'token=secret を使って集計する' });
  const saved = teachingStore.save(root, 'report', session);
  assert.strictEqual(saved.messages[0].text, 'token=*** を使って集計する');
  assert.deepStrictEqual(teachingStore.load(root, 'report'), saved);
  assert.ok(fs.existsSync(path.join(dir, 'teaching.json')));
  assert.ok(!fs.existsSync(path.join(dir, 'teaching.json.tmp')));
});

test('まだ生成物がない下書きも仕事一覧へ返す', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-teaching-list-'));
  teachingStore.create(root, { machine: 'draft-job', title: '請求確認', purpose: '請求内容を確認する' });

  assert.deepStrictEqual(teachingStore.list(root), [{
    machine: 'draft-job',
    title: '請求確認',
    purpose: '請求内容を確認する',
    status: 'draft',
    lastTrial: null,
  }]);
});

test('重複下書きと壊れた保存内容を安全に扱う', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-teaching-errors-'));
  assert.deepStrictEqual(teachingStore.list(root), []);
  teachingStore.create(root, { machine: 'draft', purpose: '確認する' });
  assert.throws(() => teachingStore.create(root, { machine: 'draft', purpose: '重複' }), /既に/);
  fs.writeFileSync(path.join(root, '.statemachine', 'draft', 'teaching.json'), '{broken', 'utf8');
  assert.throws(() => teachingStore.load(root, 'draft'), /読み取れません/);
  assert.deepStrictEqual(teachingStore.list(root), [], '壊れた1件で一覧全体を失わない');
});
