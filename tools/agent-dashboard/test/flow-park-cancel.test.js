'use strict';

// park & poll と cancel の viewer 側サポートを検証する軽量テスト（追加依存なし）。
//   - readRun: waits/<id>.json（生存 lease）を「承認待ち(parked)」ノードとして導出。
//              lease 失効は pending へ縮退。throttled は起票見送りとして区別。
//   - cancelRun: cancel マーカー設置＋meta を canceled に確定＋waits 掃除（agent-flow の cmd_cancel と同形）。
//   - TERMINAL に canceled を含む（canceled run を「応答なし」に誤分類しない）。

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

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj, null, 2), 'utf8');
}

// 1 ノードの run を作る。graph + meta（running）。
function makeRun(busDir, runId, nodeId = 'n1') {
  const runDir = path.join(busDir, 'runs', runId);
  writeJson(path.join(runDir, 'meta.json'), { request: 'r', status: 'running', created_at: '2026-01-01T00:00:00Z' });
  writeJson(path.join(runDir, 'graph.json'), { nodes: { [nodeId]: { goal: 'g', deps: [] } }, iteration: 0 });
  return runDir;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-park-'));
const bus = path.join(tmp, 'bus');

test('生存 lease の waits/ ノードは parked（承認待ち）として導出される', () => {
  const runDir = makeRun(bus, 'run-a');
  writeJson(path.join(runDir, 'waits', 'n1.json'), {
    id: 'n1', who: 'w1', throttled: false, active_seen: true,
    issue: { host: 'h', project: 'p', iid: 5, url: 'https://gl/x/-/issues/5' },
    wait_lease_until: Date.now() / 1000 + 1000,
  });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.n1.state, 'parked');
  assert.strictEqual(run.nodes.n1.parked, true);
  assert.strictEqual(run.nodes.n1.throttled, false);
  assert.strictEqual(run.nodes.n1.parkActiveSeen, true);
  assert.strictEqual(run.nodes.n1.issueUrl, 'https://gl/x/-/issues/5'); // wait 記録の issue から拾う
  assert.strictEqual(run.counts.parked, 1);
});

test('lease 失効の waits/ は pending へ縮退（parked にしない）', () => {
  const runDir = makeRun(bus, 'run-b');
  writeJson(path.join(runDir, 'waits', 'n1.json'), {
    id: 'n1', throttled: false, wait_lease_until: Date.now() / 1000 - 1, // 失効
  });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.n1.state, 'pending');
  assert.strictEqual(run.nodes.n1.parked, false);
});

test('throttled な park は起票見送りとして区別される（issue なし）', () => {
  const runDir = makeRun(bus, 'run-c');
  writeJson(path.join(runDir, 'waits', 'n1.json'), {
    id: 'n1', throttled: true, issue: null, wait_lease_until: Date.now() / 1000 + 1000,
  });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.n1.state, 'parked');
  assert.strictEqual(run.nodes.n1.throttled, true);
  assert.strictEqual(run.nodes.n1.issueUrl, null);
});

test('result があれば waits より優先（決着が正）', () => {
  const runDir = makeRun(bus, 'run-d');
  writeJson(path.join(runDir, 'results', 'n1.json'), { id: 'n1', status: 'done', output: 'ok' });
  writeJson(path.join(runDir, 'waits', 'n1.json'), {
    id: 'n1', wait_lease_until: Date.now() / 1000 + 1000,
  });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.nodes.n1.state, 'done');
});

test('cancelRun: マーカー設置＋meta canceled＋waits 掃除', () => {
  const runDir = makeRun(bus, 'run-e');
  writeJson(path.join(runDir, 'waits', 'n1.json'), {
    id: 'n1', issue: { host: 'h', project: 'p', iid: 9, url: 'u' },
    wait_lease_until: Date.now() / 1000 + 1000,
  });
  const res = flow.cancelRun(bus, 'run-e', { reason: 'テスト停止' });
  assert.strictEqual(res.marked, true);
  assert.strictEqual(res.cleared, 1);
  assert.deepStrictEqual(res.issues, [{ host: 'h', project: 'p', iid: 9, url: 'u' }]);
  // (1) cancel マーカー
  const marker = JSON.parse(fs.readFileSync(path.join(bus, 'inbox', 'cancels', 'run-e.json'), 'utf8'));
  assert.strictEqual(marker.id, 'run-e');
  assert.strictEqual(marker.close_issues, false); // viewer は追跡だけやめる（イシューは残す）
  // (2) meta が canceled
  const meta = JSON.parse(fs.readFileSync(path.join(runDir, 'meta.json'), 'utf8'));
  assert.strictEqual(meta.status, 'canceled');
  assert.strictEqual(meta.cancel_reason, 'テスト停止');
  // (3) waits 掃除
  assert.strictEqual(fs.existsSync(path.join(runDir, 'waits', 'n1.json')), false);
});

test('cancelRun: 既に終端した run には効かない（不可逆）', () => {
  const runDir = makeRun(bus, 'run-f');
  writeJson(path.join(runDir, 'meta.json'), { request: 'r', status: 'done', created_at: '2026-01-01T00:00:00Z' });
  const res = flow.cancelRun(bus, 'run-f', {});
  assert.strictEqual(res.alreadyTerminal, true);
  assert.strictEqual(res.status, 'done');
  assert.strictEqual(fs.existsSync(path.join(bus, 'inbox', 'cancels', 'run-f.json')), false); // マーカーを置かない
});

test('canceled run は readRun で終端扱い（alive=null＝応答なしにしない）', () => {
  const runDir = makeRun(bus, 'run-g');
  writeJson(path.join(runDir, 'meta.json'), { request: 'r', status: 'canceled', created_at: '2026-01-01T00:00:00Z' });
  const run = flow.readRun(runDir);
  assert.strictEqual(run.status, 'canceled');
  assert.strictEqual(run.alive, null); // TERMINAL に含む＝孤児（応答なし）判定の対象外
});

console.log(`\n${passed} passed`);
