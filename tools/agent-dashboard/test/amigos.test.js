'use strict';

// Amigos feature のテスト（Electron 不使用）。
// - ノード予算（node-budget 契約）の集計・超過判定・保存
// - ミッション読み取りビュー（ローカルバス / GitBus workdir の両形式・phase 近似）
// - agent-amigos（Python 実装・stub）が実際に生成したバスを読めるかのクロス検証
//   （python3 が無い環境ではクロス検証だけスキップ）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const budget = require('../src/features/amigos/main/budget');
const missions = require('../src/features/amigos/main/missions');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function utcDay() {
  const d = new Date();
  return (
    String(d.getUTCFullYear()) +
    String(d.getUTCMonth() + 1).padStart(2, '0') +
    String(d.getUTCDate()).padStart(2, '0')
  );
}

function writeLedger(dir, day, records) {
  fs.mkdirSync(path.join(dir, 'ledger'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'ledger', `${day}.jsonl`),
    records.map((r) => JSON.stringify(r)).join('\n') + '\n'
  );
}

function cfgFor(dir, extra) {
  return { amigos: { budgetDir: dir, busDirs: [], ...(extra || {}) } };
}

// --- ノード予算 -------------------------------------------------------------

test('ノード予算: 設定なし = 0 = 無制限（hasData も false）', () => {
  const dir = tmpdir('amigos-budget-');
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.exceeded, false);
  assert.strictEqual(u.limitSeconds, 0);
  assert.strictEqual(u.hasData, false);
});

test('ノード予算: 台帳をワークロード別に集計し合計上限で超過判定する', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'routine', seconds: 60 },
    { ts: 'x', workload: 'amigos', seconds: 30 },
    { ts: 'x', workload: 'amigos', seconds: 30 },
    'broken-line-not-json' && { ts: 'x', workload: 'project', seconds: 0 },
  ]);
  fs.appendFileSync(path.join(dir, 'ledger', `${utcDay()}.jsonl`), 'broken\n');
  budget.save(cfgFor(dir), { executionMinutes: 2, period: 'day' }); // 上限 2 分 = 120 秒
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.totals.routine, 60);
  assert.strictEqual(u.totals.amigos, 60);
  assert.strictEqual(u.totalSeconds, 120);
  assert.strictEqual(u.exceeded, true); // 120 >= 120（定常業務 + amigos の合計で超過）
});

test('ノード予算: 内訳上限は合計が無制限でも効く', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, utcDay(), [{ ts: 'x', workload: 'amigos', seconds: 61 }]);
  budget.save(cfgFor(dir), { executionMinutes: 0, workloads: { amigos: 1 } });
  const u = budget.usage(cfgFor(dir));
  assert.strictEqual(u.limitSeconds, 0);
  assert.deepStrictEqual(u.exceededWorkloads, ['amigos']);
  assert.strictEqual(u.exceeded, true);
});

test('ノード予算: period=day は今日の台帳だけを数える', () => {
  const dir = tmpdir('amigos-budget-');
  writeLedger(dir, '19990101', [{ ts: 'x', workload: 'amigos', seconds: 999 }]);
  budget.save(cfgFor(dir), { executionMinutes: 1, period: 'day' });
  assert.strictEqual(budget.usage(cfgFor(dir)).exceeded, false);
  budget.save(cfgFor(dir), { period: 'total' });
  assert.strictEqual(budget.usage(cfgFor(dir)).exceeded, true);
});

test('ノード予算: save は部分更新で config.json（契約形式）を書く', () => {
  const dir = tmpdir('amigos-budget-');
  budget.save(cfgFor(dir), { executionMinutes: 240 });
  budget.save(cfgFor(dir), { workloads: { amigos: 60 } });
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  assert.strictEqual(raw.execution_minutes, 240); // 前回の値が保持される
  assert.strictEqual(raw.workloads.amigos, 60);
  assert.strictEqual(raw.updated_by, 'dashboard');
  assert.throws(() => budget.save(cfgFor(dir), { executionMinutes: -1 }));
  assert.throws(() => budget.save(cfgFor(dir), { period: 'week' }));
});

// --- ミッション読み取りビュー -----------------------------------------------

function makeMission(dir, mid, { phaseSetup } = {}) {
  const m = path.join(dir, 'missions', mid);
  const w = (rel, data) => {
    const p = path.join(m, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, typeof data === 'string' ? data : JSON.stringify(data));
  };
  w('mission.json', {
    id: mid, title: 'テスト', goal: 'g', owner_node: 'node-a',
    posted_at: '2026-07-17T00:00:00Z',
    budget: { execution_minutes: 1, soft_ratio: 0.9, on_exhausted: 'wrap-up' },
  });
  w('roles/architect.json', { id: 'architect', title: '設計', required: true });
  w('roles/impl.json', { id: 'impl', title: '実装', required: true });
  w('roles/integrator.json', { id: 'integrator', required: true, builtin: 'integrator' });
  if (phaseSetup !== 'open') {
    w('roster.json', {
      architect: { node: 'node-a' }, impl: { node: 'node-b' }, integrator: { node: 'node-a' },
    });
    w('status/node-a--architect.json', { node: 'node-a', role: 'architect', state: 'working', turn: 2, done_round: 0 });
    w('status/node-b--impl.json', { node: 'node-b', role: 'impl', state: 'paused', turn: 1, note: '[node-budget] 超過' });
    w('events/node-a--architect.jsonl', '{"cli_seconds": 30}\n{"cli_seconds": 30}\n');
    // 未回答質問 1 件（owner 宛は数えない）
    w('inbox/architect/01-impl.json', { id: '01', from: 'impl', to: 'architect', type: 'question' });
    w('inbox/owner/02-impl.json', { id: '02', from: 'impl', to: 'owner', type: 'question' });
  }
  if (phaseSetup === 'reviewing') {
    w('deliverable/MANIFEST.json', { round: 0, partial: true, reason: 'budget' });
  }
  return m;
}

test('ミッション: ローカルバス形式を読み phase/予算/未回答/一時停止を導出する', () => {
  const bus = tmpdir('amigos-bus-');
  makeMission(bus, 'am-1');
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [bus] }));
  assert.strictEqual(ov.missions.length, 1);
  const m = ov.missions[0];
  assert.strictEqual(m.phase, 'working');
  assert.strictEqual(m.round, 0);
  assert.strictEqual(m.budget.spentSeconds, 60);
  assert.strictEqual(m.budget.limitSeconds, 60);
  assert.strictEqual(m.budget.hard, true); // ミッション予算枯渇
  assert.strictEqual(m.unanswered, 1); // owner 宛は数えない
  assert.deepStrictEqual(m.pausedRoles, ['impl']);
  const impl = m.roles.find((r) => r.id === 'impl');
  assert.strictEqual(impl.node, 'node-b');
  assert.strictEqual(impl.state, 'paused');
});

test('ミッション: 募集中（roster 未充足）は open、MANIFEST 現行ラウンドは reviewing', () => {
  const bus = tmpdir('amigos-bus-');
  makeMission(bus, 'am-open', { phaseSetup: 'open' });
  makeMission(bus, 'am-rev', { phaseSetup: 'reviewing' });
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [bus] }));
  const byId = Object.fromEntries(ov.missions.map((m) => [m.id, m]));
  assert.strictEqual(byId['am-open'].phase, 'open');
  assert.strictEqual(byId['am-rev'].phase, 'reviewing');
  assert.strictEqual(byId['am-rev'].manifest.partial, true);
});

test('ミッション: GitBus workdir 形式（mission__<mid>/）も読める', () => {
  const wd = tmpdir('amigos-wd-');
  // GitBus のクローンはリポジトリ直下が内容ルート
  const inner = tmpdir('amigos-src-');
  makeMission(inner, 'am-git');
  fs.cpSync(path.join(inner, 'missions', 'am-git'), path.join(wd, 'mission__am-git'), { recursive: true });
  const ov = missions.overview(cfgFor(tmpdir('amigos-b-'), { busDirs: [wd] }));
  assert.strictEqual(ov.missions.length, 1);
  assert.strictEqual(ov.missions[0].id, 'am-git');
});

// --- クロス検証: agent-amigos（Python 実装）が作った本物のバスを読む ----------

test('クロス検証: agent-amigos stub の実バスを dashboard リーダーで読める', () => {
  const py = spawnSync('python3', ['--version'], { encoding: 'utf8' });
  if (py.status !== 0) {
    console.log('   (python3 なし — クロス検証はスキップ)');
    return;
  }
  const work = tmpdir('amigos-x-');
  const bus = path.join(work, 'bus');
  fs.writeFileSync(path.join(work, 'design.md'), '# design\n');
  fs.writeFileSync(
    path.join(work, 'roles.json'),
    JSON.stringify({
      mission: { title: 'クロス', goal: 'g', staffing_timeout: 0,
                 convergence: { done_when: 'all-required-done' } },
      roles: [
        { id: 'architect', mission: 'a', deliverables: ['arch.md'] },
        { id: 'impl', mission: 'b', deliverables: ['main.py'], collaborates_with: ['architect'] },
      ],
    })
  );
  const entry = path.join(__dirname, '..', '..', 'agent-amigos', 'agent-amigos.py');
  const env = { ...process.env, AGENT_AMIGOS_NODE: 'owner-node', AGENT_AMIGOS_STUB_COST: '0.01',
                AGENT_BUDGET_DIR: path.join(work, 'nb') };
  let r = spawnSync('python3', [entry, 'post', '--bus', bus, '--design',
    path.join(work, 'design.md'), '--roles', path.join(work, 'roles.json'),
    '--mission-id', 'am-x', '--serve', '--agent-cli', 'stub', '--cycles', '10',
    '--interval', '0'], { encoding: 'utf8', env, cwd: work });
  assert.strictEqual(r.status, 0, r.stderr);
  const ov = missions.overview(cfgFor(path.join(work, 'nb'), { busDirs: [bus] }));
  assert.strictEqual(ov.missions.length, 1);
  const m = ov.missions[0];
  assert.strictEqual(m.id, 'am-x');
  assert.strictEqual(m.phase, 'reviewing'); // stub 完走 → 受入待ち
  assert.ok(m.roles.every((r2) => r2.node), '全ロールに担当が付く');
  assert.ok(m.budget.spentSeconds > 0, 'events の cli_seconds が読める');
  // ノード予算の台帳（workload=amigos）も agent-amigos が記帳している
  const u = budget.usage(cfgFor(path.join(work, 'nb')));
  assert.ok((u.totals.amigos || 0) > 0, '共有台帳に amigos の記帳がある');
});

console.log(`\n${passed} amigos tests passed`);
