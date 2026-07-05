'use strict';

// flow.js の run-id 解析（parseRunId）と readRun の関係性フィールドを検証する軽量テスト。
// 追加依存なしで `node test/flow-relationship.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- parseRunId ---
test('parseRunId は req- 形式を taskId / retries / lineage に分解する', () => {
  const p = flow.parseRunId('req-a1b2c3d4-TASK-12-r2');
  assert.strictEqual(p.taskId, 'TASK-12');
  assert.strictEqual(p.retries, 2);
  assert.strictEqual(p.rev, null);
  assert.strictEqual(p.lineageId, 'req-a1b2c3d4-TASK-12');
});

test('parseRunId は revise 世代（-v）も拾う', () => {
  const p = flow.parseRunId('req-a1b2c3d4-TASK-12-r0-v3');
  assert.strictEqual(p.taskId, 'TASK-12');
  assert.strictEqual(p.retries, 0);
  assert.strictEqual(p.rev, '3');
  assert.strictEqual(p.lineageId, 'req-a1b2c3d4-TASK-12'); // 系統は同じ（リトライ/リバイズを束ねる）
});

test('parseRunId は素の run-（手動/単発）を taskId 無しにする', () => {
  const p = flow.parseRunId('run-20260705-134501-8213');
  assert.strictEqual(p.taskId, null);
  assert.strictEqual(p.lineageId, null);
});

test('同一タスクの r0 と r1 は同じ lineageId になる', () => {
  const a = flow.parseRunId('req-ff00ff00-fix.bug-r0');
  const b = flow.parseRunId('req-ff00ff00-fix.bug-r1');
  assert.strictEqual(a.lineageId, b.lineageId);
  assert.notStrictEqual(a.retries, b.retries);
});

// --- readRun が関係性フィールドを surface する ---
test('readRun は run-id 由来の taskId/lineageId と meta.inherited_from を返す', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-flow-'));
  try {
    const runId = 'req-a1b2c3d4-TASK-9-r1';
    const runDir = path.join(tmp, 'runs', runId);
    fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
    fs.writeFileSync(
      path.join(runDir, 'meta.json'),
      JSON.stringify({ status: 'running', request: 'do it', inherited_from: 'req-a1b2c3d4-TASK-9-r0' })
    );
    fs.writeFileSync(
      path.join(runDir, 'graph.json'),
      JSON.stringify({ nodes: { t1: { goal: 'g', deps: [] } } })
    );
    const run = flow.readRun(runDir);
    assert.strictEqual(run.taskId, 'TASK-9');
    assert.strictEqual(run.retries, 1);
    assert.strictEqual(run.lineageId, 'req-a1b2c3d4-TASK-9');
    assert.strictEqual(run.inheritedFrom, 'req-a1b2c3d4-TASK-9-r0');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
