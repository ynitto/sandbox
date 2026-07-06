'use strict';

// actions.enqueueManyToInbox（バックログ一括追加）の軽量テスト。1 つの inbox ファイルに
// JSON 配列を書き、kiro-projects の ingest_inbox が 1 件ずつ backlog 化できる形にする。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const actions = require('../src/main/actions');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('enqueueManyToInbox は複数タスクを 1 つの JSON 配列で投入する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-enq-'));
  try {
    const res = actions.enqueueManyToInbox(tmp, [
      { title: 'T1', verify: 'grep -q A f', priority: '2', after: 'T0' },
      { title: 'T2', accept: '自然文の条件', level: 'assisted' },
      { title: '' }, // タイトル無しは無視される
    ]);
    assert.strictEqual(res.count, 2);
    const data = JSON.parse(fs.readFileSync(res.file, 'utf8'));
    assert.ok(Array.isArray(data), 'inbox ファイルは JSON 配列');
    assert.strictEqual(data.length, 2);
    assert.strictEqual(data[0].title, 'T1');
    assert.strictEqual(data[0].verify, 'grep -q A f'); // 生値をそのまま保持（取り込み時に kiro-projects が正規化）
    assert.strictEqual(data[0].priority, 2);
    assert.strictEqual(data[0].after, 'T0');
    assert.strictEqual(data[1].level, 'assisted');
    assert.ok(!('priority' in data[1]), '優先度 0 は書かない');
    assert.ok(path.basename(res.file).startsWith('viewer-batch-'));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('enqueueManyToInbox はタイトルのある行が無ければ拒否する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-enq-'));
  try {
    assert.throws(() => actions.enqueueManyToInbox(tmp, [{ title: '' }, {}]), /タイトルのあるタスク/);
    assert.throws(() => actions.enqueueManyToInbox(tmp, []), /タイトルのあるタスク/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
