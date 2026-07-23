'use strict';

// 委譲公示板（agent-board）ターゲットのテスト（Electron 不使用）。
// - contract: post 封筒が additive な requires / speculation を保持する
// - board アダプタ: post/award/cancel のファイル投函、板ファイル → 正規化ビュー（入札の勝者判定・フェーズ）
// - IPC 配線: target='board' で post/award/cancel が板リポジトリへ届き、list が board を含める

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const contract = require('../src/features/delegation/main/contract');
const boardAdapter = require('../src/features/delegation/main/board-adapter');
const delegationIpc = require('../src/features/delegation/main/ipc');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function ipcHandlers(cfg) {
  const handlers = {};
  delegationIpc.registerIpc({
    handle: (ch, fn) => { handlers[ch] = fn; },
    loadConfig: () => cfg,
    saveConfig: () => cfg,
  });
  return handlers;
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj), 'utf8');
}

// --- contract: additive ブロックの保持 --------------------------------------

test('封筒: post が requires / speculation を保持する（board が解釈する additive）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'flow', goal: '実装',
    requires: { tags: ['python'], repos: ['app'] },
    speculation: { max_runners: 2, resolve: 'first-valid' },
  });
  assert.deepStrictEqual(env.requires, { tags: ['python'], repos: ['app'] });
  assert.deepStrictEqual(env.speculation, { max_runners: 2, resolve: 'first-valid' });
});

// --- board アダプタ ---------------------------------------------------------

test('board: submitPost が delegations/<id>/post.json を書く（冪等）', () => {
  const repo = tmpdir('deleg-board-');
  const env = contract.buildEnvelope('post', { id: 'dg-1', workload: 'flow', goal: 'g' });
  const res = boardAdapter.submitPost(repo, env);
  assert.strictEqual(res.file, path.join(repo, 'delegations', 'dg-1', 'post.json'));
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.id, 'dg-1');
  // 再投函は同一公示（二重公示防止）
  const res2 = boardAdapter.submitPost(repo, env);
  assert.ok(res2.duplicate);
});

test('board: toView が open → working → done のフェーズと勝者を導く', () => {
  const repo = tmpdir('deleg-board-');
  const dir = path.join(repo, 'delegations', 'dg-2');
  writeJson(path.join(dir, 'post.json'),
    { op: 'post', version: 1, id: 'dg-2', workload: 'flow', goal: 'g', title: 'T' });
  // open: 入札なし
  let v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'open');
  assert.strictEqual(v.units[0].bids.length, 0);

  // 入札 2 件（ts 昇順で pc-b が勝者）
  const soon = 9999999999;
  writeJson(path.join(dir, 'bids', 'pc-b.json'), { who: 'pc-b', ts: 100, lease_until: soon });
  writeJson(path.join(dir, 'bids', 'pc-a.json'), { who: 'pc-a', ts: 200, lease_until: soon });
  writeJson(path.join(dir, 'status', 'pc-b.json'), { who: 'pc-b', state: 'working' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'working');
  assert.strictEqual(v.units[0].assignee, 'pc-b');
  const winner = v.units[0].bids.find((b) => b.state === 'winner');
  assert.strictEqual(winner.who, 'pc-b');

  // done: result.json
  writeJson(path.join(dir, 'result.json'), { winner: 'pc-b', status: 'done', resolved_at: 'x' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'done');
  assert.strictEqual(v.result.by, 'pc-b');
});

test('board: 失効した入札は expired・cancelled マーカーで cancelled', () => {
  const repo = tmpdir('deleg-board-');
  const dir = path.join(repo, 'delegations', 'dg-3');
  writeJson(path.join(dir, 'post.json'), { id: 'dg-3', workload: 'flow', goal: 'g' });
  writeJson(path.join(dir, 'bids', 'pc-a.json'), { who: 'pc-a', ts: 1, lease_until: 5 });
  let v = boardAdapter.toView(dir, 1000);  // now=1000 > lease 5 → 失効
  assert.strictEqual(v.units[0].bids[0].state, 'expired');
  assert.strictEqual(v.phase, 'open');
  writeJson(path.join(dir, 'cancelled.json'), { cancelled_at: 'x' });
  v = boardAdapter.toView(dir, 1000);
  assert.strictEqual(v.phase, 'cancelled');
});

// --- IPC 配線 ---------------------------------------------------------------

test('IPC: target=board の post は板リポジトリへ投函する', () => {
  const repo = tmpdir('deleg-board-');
  const cfg = { delegation: { boardRepos: [repo] } };
  const h = ipcHandlers(cfg);
  const res = h['delegation:post']({
    target: 'board', boardRepo: repo, workload: 'flow', goal: '実装',
    requires: { repos: ['app'] },
  });
  assert.strictEqual(path.dirname(res.file), path.join(repo, 'delegations', res.id));
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.deepStrictEqual(rec.requires, { repos: ['app'] });
});

test('IPC: target=board の award / cancel が板ファイルを書く', () => {
  const repo = tmpdir('deleg-board-');
  const h = ipcHandlers({ delegation: { boardRepos: [repo] } });
  const aw = h['delegation:award']({ target: 'board', boardRepo: repo, id: 'dg-9', node: 'pc-a' });
  const awRec = JSON.parse(fs.readFileSync(aw.file, 'utf8'));
  assert.strictEqual(awRec.node, 'pc-a');
  const cn = h['delegation:cancel']({
    target: 'board', boardRepo: repo, id: 'dg-9', workload: 'flow', reason: 'stop',
  });
  const cnRec = JSON.parse(fs.readFileSync(cn.file, 'utf8'));
  assert.strictEqual(cnRec.reason, 'stop');
});

test('IPC: list は board リポジトリの委譲も揃えて返す', () => {
  const repo = tmpdir('deleg-board-');
  writeJson(path.join(repo, 'delegations', 'dg-l', 'post.json'),
    { id: 'dg-l', workload: 'amigos', goal: 'g', title: 'L' });
  const cfg = {
    amigos: { homeDirs: [], busDirs: [] }, projects: { roots: [] },
    delegation: { flowBusDirs: [], boardRepos: [repo] },
  };
  const h = ipcHandlers(cfg);
  const { items, errors } = h['delegation:list']();
  assert.deepStrictEqual(errors, []);
  const item = items.find((i) => i.id === 'dg-l');
  assert.ok(item, 'board の委譲がビューに含まれる');
  assert.strictEqual(item.target, 'board');
  assert.strictEqual(item.boardRepo, repo);
});

console.log(`\n${passed} tests passed`);
