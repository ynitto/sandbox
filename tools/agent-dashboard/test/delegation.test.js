'use strict';

// 委譲契約（delegation 契約）のテスト（Electron 不使用）。
// - 封筒の検証（op / id / workload・エンジン非対称の fail-fast）
// - amigos アダプタ: 封筒 → amigos-command（正典 schemas/amigos-command.schema.json と一致）、
//   ミッション + assignments/ → 正規化ビュー（入札の勝者/応募/失効判定）
// - flow アダプタ: 封筒 → inbox（submit_request と同形）、run → 正規化ビュー（先着=勝者1件・stale）
// - IPC 配線: post/award/accept/reject/cancel が正しい投函先へ届く

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const contract = require('../src/features/delegation/main/contract');
const amigosAdapter = require('../src/features/delegation/main/amigos-adapter');
const flowAdapter = require('../src/features/delegation/main/flow-adapter');
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

// amigos-command.schema.json の enum と一致していることを確認する（両側テストで契約一致を担保）。
const AMIGOS_COMMANDS = new Set(
  JSON.parse(
    fs.readFileSync(
      path.join(__dirname, '..', '..', '..', 'schemas', 'amigos-command.schema.json'),
      'utf8'
    )
  ).properties.command.enum
);

// --- 封筒の検証 -------------------------------------------------------------

test('封筒: 正しい post を正規化する（既定値の補完込み）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'amigos',
    goal: '目標',
    id: 'dg-x',
    engine: { amigos: { roles: [{ id: 'impl' }] } },
  });
  assert.strictEqual(env.op, 'post');
  assert.strictEqual(env.version, 1);
  assert.strictEqual(env.id, 'dg-x');
  assert.strictEqual(env.policy.assignment, 'first-come');
  assert.strictEqual(env.policy.staffing_timeout_sec, 600);
  assert.strictEqual(env.budget.per_unit_turns, 30);
  assert.ok(env.requested_at, 'requested_at は補完される');
});

test('封筒: id が無ければ dg- 形式を採番する', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'flow', goal: 'g',
  }, new Date(Date.UTC(2026, 6, 19, 1, 2, 3)));
  assert.strictEqual(env.id, `dg-20260719010203-${env.id.slice(-4)}`);
  assert.ok(/^dg-20260719010203-[0-9a-f]{4}$/.test(env.id));
});

test('封筒: 不正な id / workload / op を弾く', () => {
  assert.throws(() => contract.validateEnvelope({ op: 'post', version: 1, id: 'bad id', workload: 'flow', goal: 'g' }), /不正な id/);
  assert.throws(() => contract.validateEnvelope({ op: 'post', version: 1, id: 'ok', workload: 'nope', goal: 'g' }), /不正な workload/);
  assert.throws(() => contract.validateEnvelope({ op: 'nope', version: 1, id: 'ok', workload: 'flow' }), /不正な op/);
  assert.throws(() => contract.validateEnvelope({ op: 'post', version: 2, id: 'ok', workload: 'flow', goal: 'g' }), /version/);
});

test('封筒: flow × owner-picks は投函前に拒否する（D4）', () => {
  assert.throws(
    () => contract.buildEnvelope('post', { workload: 'flow', goal: 'g', policy: { assignment: 'owner-picks' } }),
    /owner-picks/
  );
});

test('封筒: flow の award / accept / reject は v1 で拒否する（D5）', () => {
  assert.throws(() => contract.buildEnvelope('award', { workload: 'flow', id: 'x', unit: 'a', node: 'n' }), /amigos のみ/);
  assert.throws(() => contract.buildEnvelope('accept', { workload: 'flow', id: 'x' }), /amigos のみ/);
  assert.throws(() => contract.buildEnvelope('reject', { workload: 'flow', id: 'x', feedback: 'f' }), /amigos のみ/);
});

test('封筒: amigos の post は roles 必須', () => {
  assert.throws(
    () => contract.buildEnvelope('post', { workload: 'amigos', goal: 'g' }),
    /roles/
  );
});

test('封筒: reject は feedback 必須', () => {
  assert.throws(() => contract.buildEnvelope('reject', { workload: 'amigos', id: 'x' }), /feedback/);
});

// --- amigos アダプタ: 封筒 → コマンド -------------------------------------

test('amigos: post → command（mission_id に共通 id を採用・mission へ写像）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'amigos',
    id: 'dg-abc',
    title: '件名',
    goal: '目標',
    policy: { assignment: 'owner-picks', staffing_timeout_sec: 120 },
    acceptance: 'agent',
    budget: { execution_minutes: 30, per_unit_turns: 10 },
    engine: { amigos: { roles: [{ id: 'impl' }], mission: { convergence: { review_rounds: 3 } } } },
  });
  const cmd = amigosAdapter.toCommand(env);
  assert.ok(AMIGOS_COMMANDS.has(cmd.command), 'command は amigos-command の enum');
  assert.strictEqual(cmd.command, 'post');
  assert.strictEqual(cmd.mission_id, 'dg-abc', '共通 id を mission_id に採用（対応表なし）');
  assert.strictEqual(cmd.mission.assignment_policy, 'owner-picks');
  assert.strictEqual(cmd.mission.staffing_timeout, 120);
  assert.strictEqual(cmd.mission.acceptance, 'agent');
  assert.strictEqual(cmd.mission.budget.execution_minutes, 30);
  assert.strictEqual(cmd.mission.budget.per_role_turns, 10);
  assert.strictEqual(cmd.mission.convergence.review_rounds, 3, 'engine.amigos.mission が最後に勝つ');
  assert.deepStrictEqual(cmd.roles, [{ id: 'impl' }]);
});

test('amigos: design 省略時は goal + 参照から合成する（D）', () => {
  const env = contract.buildEnvelope('post', {
    workload: 'amigos', id: 'dg-d', goal: '目標X',
    references: [{ url: 'https://git/ref' }],
    engine: { amigos: { roles: [{ id: 'r' }] } },
  });
  const cmd = amigosAdapter.toCommand(env);
  assert.ok(cmd.design.includes('目標X'));
  assert.ok(cmd.design.includes('https://git/ref'));
  assert.ok(cmd.design.includes('自動生成'), '合成の但し書きが入る');
});

test('amigos: award/accept/reject/cancel → 対応する command', () => {
  const mk = (op, extra) => contract.buildEnvelope(op, { workload: 'amigos', id: 'dg-1', ...extra });
  assert.deepStrictEqual(amigosAdapter.toCommand(mk('award', { unit: 'impl', node: 'nodeA' })),
    { command: 'assign', mission: 'dg-1', role: 'impl', node: 'nodeA' });
  assert.deepStrictEqual(amigosAdapter.toCommand(mk('accept', {})),
    { command: 'accept', mission: 'dg-1' });
  assert.deepStrictEqual(amigosAdapter.toCommand(mk('reject', { feedback: 'なおして' })),
    { command: 'reject', mission: 'dg-1', feedback: 'なおして' });
  assert.deepStrictEqual(amigosAdapter.toCommand(mk('cancel', { reason: 'やめ' })),
    { command: 'cancel', mission: 'dg-1', reason: 'やめ' });
  for (const c of ['assign', 'accept', 'reject', 'cancel']) {
    assert.ok(AMIGOS_COMMANDS.has(c), `${c} は amigos-command の enum`);
  }
});

// --- amigos アダプタ: 入札ビュー -------------------------------------------

function writeAssignment(dir, role, node, rec) {
  const d = path.join(dir, 'assignments', role);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, `${node}.json`), JSON.stringify({ node, ...rec }));
}

test('amigos: 入札ビュー — (ts,node) 最小が勝者・失効は expired・応募は applied', () => {
  const dir = tmpdir('deleg-amigos-');
  fs.writeFileSync(path.join(dir, 'mission.json'),
    JSON.stringify({ id: 'm1', assignment_policy: 'owner-picks' }));
  const now = 1000;
  // owner-picks 未確定（roster なし）: 2 応募 + 1 失効
  writeAssignment(dir, 'impl', 'nodeB', { ts: 20, lease_until: now + 100 });
  writeAssignment(dir, 'impl', 'nodeA', { ts: 10, lease_until: now + 100 });
  writeAssignment(dir, 'impl', 'nodeC', { ts: 5, lease_until: now - 1 }); // 失効

  const summary = {
    id: 'm1', dir, title: 'M1', goal: 'g', phase: 'open', postedAt: '2026-07-19T00:00:00Z',
    roles: [{ id: 'impl', title: '実装', node: null, state: null, done: false }],
    budget: { spentSeconds: 0, limitSeconds: 0 },
  };
  const view = amigosAdapter.toView(summary, now);
  assert.strictEqual(view.workload, 'amigos');
  assert.strictEqual(view.native_id, 'm1');
  assert.strictEqual(view.bids_open, true, 'owner-picks で未確定応募あり → 落札待ち');
  const bids = view.units[0].bids;
  const byWho = Object.fromEntries(bids.map((b) => [b.who, b.state]));
  assert.strictEqual(byWho.nodeA, 'winner', '(ts,node) 最小の生存応募が勝者');
  assert.strictEqual(byWho.nodeB, 'applied', '他の生存応募は applied');
  assert.strictEqual(byWho.nodeC, 'expired', 'lease 失効は expired');
  assert.strictEqual(view.units[0].state, 'open');
});

test('amigos: roster 確定があればその node が勝者・他は lost', () => {
  const dir = tmpdir('deleg-amigos-');
  fs.writeFileSync(path.join(dir, 'mission.json'),
    JSON.stringify({ id: 'm2', assignment_policy: 'owner-picks' }));
  const now = 1000;
  writeAssignment(dir, 'impl', 'nodeA', { ts: 10, lease_until: now + 100 });
  writeAssignment(dir, 'impl', 'nodeB', { ts: 5, lease_until: now + 100 }); // ts は小さいが確定は A
  const summary = {
    id: 'm2', dir, title: 'M2', goal: 'g', phase: 'working', postedAt: '2026-07-19T00:00:00Z',
    roles: [{ id: 'impl', title: '実装', node: 'nodeA', state: 'working', done: false }],
    budget: { spentSeconds: 0, limitSeconds: 0 },
  };
  const view = amigosAdapter.toView(summary, now);
  const byWho = Object.fromEntries(view.units[0].bids.map((b) => [b.who, b.state]));
  assert.strictEqual(byWho.nodeA, 'winner', 'roster 確定が (ts) より優先');
  assert.strictEqual(byWho.nodeB, 'lost');
  assert.strictEqual(view.units[0].state, 'claimed');
  assert.strictEqual(view.bids_open, false, '確定済みは落札待ちでない');
});

test('amigos: phase 写像（integrating→working）と progress', () => {
  const dir = tmpdir('deleg-amigos-');
  fs.writeFileSync(path.join(dir, 'mission.json'), JSON.stringify({ id: 'm3' }));
  const summary = {
    id: 'm3', dir, title: 'M3', goal: 'g', phase: 'integrating', postedAt: '2026-07-19T00:00:00Z',
    roles: [
      { id: 'a', title: 'A', node: 'n1', state: 'working', done: true },
      { id: 'b', title: 'B', node: null, state: null, done: false },
    ],
    budget: { spentSeconds: 120, limitSeconds: 600 },
  };
  const view = amigosAdapter.toView(summary, 1000);
  assert.strictEqual(view.phase, 'working', 'integrating は working に畳む');
  assert.deepStrictEqual(view.progress, { units_total: 2, units_done: 1, units_failed: 0, units_open: 1 });
  assert.deepStrictEqual(view.budget, { spent_seconds: 120, limit_minutes: 10 });
});

// --- flow アダプタ: 封筒 → inbox -------------------------------------------

test('flow: post → inbox/<id>.json（submit_request と同形・design は request に前置）', () => {
  const busDir = tmpdir('deleg-flow-');
  const env = contract.buildEnvelope('post', {
    workload: 'flow', id: 'dg-flow-1', goal: '実装して', design: '# 設計\n手順',
    workspace: { url: 'https://git/ws', base: 'main' },
    references: [{ url: 'https://git/ref' }],
    engine: { flow: { executor: 'gitlab', inherit_from: 'req-old' } },
    priority: 'high',
  });
  const res = flowAdapter.submitPost(busDir, env);
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.id, 'dg-flow-1', '共通 id を req-id に採用');
  assert.ok(rec.request.includes('実装して'));
  assert.ok(rec.request.includes('## 設計'), 'design を request に前置');
  assert.strictEqual(rec.submitter, 'dashboard');
  assert.deepStrictEqual(rec.workspace, { url: 'https://git/ws', base: 'main' });
  assert.deepStrictEqual(rec.references, [{ url: 'https://git/ref' }]);
  assert.strictEqual(rec.inherit_from, 'req-old');
  assert.strictEqual(rec.executor, 'gitlab');
  assert.strictEqual(rec.priority, 'high', 'priority は前方互換 passthrough');
  assert.ok(rec.submitted_at, 'submitted_at がある');
});

// --- flow アダプタ: run ビュー ---------------------------------------------

test('flow: run ビュー — 先着は勝者1件・parked は waiting・stale フラグ', () => {
  const run = {
    runId: 'req-x-t1-r0', status: 'running', taskId: 't1', request: '目標',
    alive: false, updatedAt: '2026-07-19T01:00:00Z', createdAt: '2026-07-19T00:00:00Z',
    final: null,
    nodes: {
      n1: { id: 'n1', kind: 'work', state: 'claimed', who: 'wA', heartbeatAt: 'h', leaseUntil: 2000 },
      n2: { id: 'n2', kind: 'verify', state: 'parked', who: 'wB' },
      n3: { id: 'n3', kind: 'work', state: 'done', who: 'wC' },
      n4: { id: 'n4', kind: 'work', state: 'pending', who: null },
    },
  };
  const view = flowAdapter.toView(run);
  assert.strictEqual(view.workload, 'flow');
  assert.strictEqual(view.phase, 'waiting', 'parked ノードがあれば waiting');
  assert.strictEqual(view.stale, true, '非終端 + alive=false は stale');
  assert.strictEqual(view.bids_open, false, 'flow は先着（落札待ちを持たない）');
  const byUnit = Object.fromEntries(view.units.map((u) => [u.unit, u]));
  assert.strictEqual(byUnit.n1.state, 'claimed');
  assert.strictEqual(byUnit.n1.bids.length, 1);
  assert.strictEqual(byUnit.n1.bids[0].state, 'winner');
  assert.strictEqual(byUnit.n2.state, 'waiting');
  assert.strictEqual(byUnit.n3.state, 'done');
  assert.strictEqual(byUnit.n4.state, 'open');
  assert.deepStrictEqual(view.progress, { units_total: 4, units_done: 1, units_failed: 0, units_open: 1 });
  assert.strictEqual(view.budget, null, 'flow の予算は node-budget 契約が担う');
});

test('flow: 終端 run の phase 写像（canceled→cancelled）と stale=false', () => {
  const view = flowAdapter.toView({
    runId: 'r', status: 'canceled', nodes: {}, alive: null, final: null,
  });
  assert.strictEqual(view.phase, 'cancelled');
  assert.strictEqual(view.stale, false, '終端は stale にしない');
});

// --- IPC 配線 ---------------------------------------------------------------

function makeHome() {
  const home = tmpdir('deleg-home-');
  fs.writeFileSync(path.join(home, 'agent-amigos.yaml'), 'node_id: nodeA\nbus: .\n');
  return home;
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

test('IPC: amigos post は発見済みホームの commands/ へ投函する', () => {
  const home = makeHome();
  const cfg = { amigos: { homeDirs: [home], busDirs: [] }, projects: { roots: [] }, delegation: {} };
  const h = ipcHandlers(cfg);
  const res = h['delegation:post']({
    workload: 'amigos', goal: '目標', title: 'T',
    home, engine: { amigos: { roles: [{ id: 'impl' }] } },
  });
  assert.strictEqual(res.workload, 'amigos');
  assert.ok(res.file && fs.existsSync(res.file), 'commands ファイルが書かれる');
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.command, 'post');
  assert.strictEqual(rec.mission_id, res.id);
  assert.strictEqual(path.dirname(res.file),
    path.join(home, '.agent', 'agent-amigos', 'commands'));
});

test('IPC: flow post はバスの inbox/ へ投函する', () => {
  const busDir = tmpdir('deleg-flowbus-');
  const cfg = { delegation: { flowBusDirs: [busDir] } };
  const h = ipcHandlers(cfg);
  const res = h['delegation:post']({ workload: 'flow', goal: '実装', busDir });
  assert.strictEqual(res.workload, 'flow');
  assert.strictEqual(path.dirname(res.file), path.join(busDir, 'inbox'));
  const rec = JSON.parse(fs.readFileSync(res.file, 'utf8'));
  assert.strictEqual(rec.id, res.id);
  assert.ok(rec.request.includes('実装'));
});

test('IPC: list は両エンジンのビューを揃えて返す', () => {
  // amigos ミッション（ローカルバス）
  const home = makeHome();
  const busDir = home; // bus: . → ホーム直下
  const mdir = path.join(busDir, 'missions', 'am1');
  fs.mkdirSync(path.join(mdir, 'roles'), { recursive: true });
  fs.writeFileSync(path.join(mdir, 'mission.json'),
    JSON.stringify({ id: 'am1', owner_node: 'nodeA', posted_at: '2026-07-19T02:00:00Z', title: 'AM1', goal: 'g' }));
  fs.writeFileSync(path.join(mdir, 'roles', 'impl.json'),
    JSON.stringify({ id: 'impl', title: '実装', required: true }));

  // flow run
  const flowBus = tmpdir('deleg-flowbus-');
  const rdir = path.join(flowBus, 'runs', 'req-a-t1-r0');
  fs.mkdirSync(rdir, { recursive: true });
  fs.writeFileSync(path.join(rdir, 'meta.json'),
    JSON.stringify({ status: 'running', request: 'やること', created_at: '2026-07-19T01:00:00Z', orch_lease_until: 9999999999 }));
  fs.writeFileSync(path.join(rdir, 'graph.json'),
    JSON.stringify({ nodes: { n1: { goal: 'x', kind: 'work', deps: [] } } }));

  const cfg = {
    amigos: { homeDirs: [home], busDirs: [] }, projects: { roots: [] },
    delegation: { flowBusDirs: [flowBus] },
  };
  const h = ipcHandlers(cfg);
  const { items, errors } = h['delegation:list']();
  assert.deepStrictEqual(errors, [], 'エラーなし');
  const am = items.find((i) => i.id === 'am1');
  const fl = items.find((i) => i.id === 'req-a-t1-r0');
  assert.ok(am && am.workload === 'amigos');
  assert.ok(fl && fl.workload === 'flow');
  assert.strictEqual(am.units.length, 1);
  assert.strictEqual(fl.units.length, 1);
});

console.log(`\n${passed} tests passed`);
