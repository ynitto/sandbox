'use strict';

// フロー run のアーカイブ（ビュアー側保管庫）のテスト。追加依存なしで
// `node test/flow-archive.test.js` で走る。
//   - archiveRunSnapshot: run のスナップショット保存（同一内容の再保存はスキップ）
//   - listArchivedRuns / readArchivedRun: bus から掃除された run をアーカイブから読める
//   - 保持上限（prune）はここでは対象外（ARCHIVE_KEEP 件の頭打ちのみの単純規則）

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

// bus に 1 run（2 ノード・両方 done・meta.status=done）を作る
function mkBus(runId) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-arch-'));
  const busDir = path.join(root, 'bus');
  const runDir = path.join(busDir, 'runs', runId);
  fs.mkdirSync(path.join(runDir, 'results'), { recursive: true });
  fs.mkdirSync(path.join(runDir, 'events'), { recursive: true });
  fs.writeFileSync(
    path.join(runDir, 'meta.json'),
    JSON.stringify({
      status: 'done',
      request: 'CSV を要約する',
      created_at: '2026-07-11T00:00:00Z',
      updated_at: '2026-07-11T00:10:00Z',
    }),
    'utf8'
  );
  fs.writeFileSync(
    path.join(runDir, 'graph.json'),
    JSON.stringify({ nodes: { a: { goal: 'A', deps: [] }, b: { goal: 'B', deps: ['a'] } } }),
    'utf8'
  );
  for (const id of ['a', 'b']) {
    fs.writeFileSync(
      path.join(runDir, 'results', `${id}.json`),
      JSON.stringify({ status: 'done', who: 'w1', finished_at: '2026-07-11T00:05:00Z' }),
      'utf8'
    );
  }
  fs.writeFileSync(
    path.join(runDir, 'events', 'w1.jsonl'),
    `${JSON.stringify({ ts: '2026-07-11T00:01:00Z', who: 'w1', kind: 'claimed', node: 'a' })}\n`,
    'utf8'
  );
  return { root, busDir, runDir };
}

test('archiveRunSnapshot → bus 掃除後も listArchivedRuns / readArchivedRun で読める', () => {
  const runId = 'req-abc123-T1-r0';
  const { root, busDir, runDir } = mkBus(runId);
  const archRoot = path.join(root, 'archive');
  try {
    const [run] = flow.listRuns(busDir);
    assert.strictEqual(run.runId, runId);
    assert.strictEqual(flow.archiveRunSnapshot(archRoot, busDir, run), true, '初回は保存する');
    assert.strictEqual(flow.archiveRunSnapshot(archRoot, busDir, run), false, '同一内容はスキップ');

    // kiro-flow の掃除を模して bus から run を消す
    fs.rmSync(runDir, { recursive: true, force: true });
    assert.strictEqual(flow.listRuns(busDir).length, 0);

    const archived = flow.listArchivedRuns(archRoot, busDir);
    assert.strictEqual(archived.length, 1);
    assert.strictEqual(archived[0].runId, runId);
    assert.strictEqual(archived[0].archived, true);
    assert.strictEqual(archived[0].status, 'done');
    assert.strictEqual(archived[0].alive, null, '孤児と誤表示しない');
    assert.strictEqual(archived[0].taskId, 'T1', 'req- 形式の系統情報も保たれる');

    const snap = flow.readArchivedRun(archRoot, busDir, runId);
    assert.ok(snap && snap.run && snap.run.runId === runId);
    assert.strictEqual(snap.run.counts.done, 2, 'ノード状態のスナップショットを保持');
    assert.ok(Array.isArray(snap.events) && snap.events.length === 1, 'イベントも保存される');
    assert.ok(snap.nodeEvents && snap.nodeEvents.a, 'ノード別タイムラインも保存される');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('readArchivedRun は不正な runId / 未知の runId に null を返す', () => {
  const { root, busDir } = mkBus('run-1');
  const archRoot = path.join(root, 'archive');
  try {
    assert.strictEqual(flow.readArchivedRun(archRoot, busDir, '../etc'), null);
    assert.strictEqual(flow.readArchivedRun(archRoot, busDir, 'unknown'), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
