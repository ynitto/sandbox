'use strict';

// ヘッドレス資源制御（scripts/resource-control.js）のテスト。
//
// このスクリプトが S8 の成果物そのもの——「dashboard を開いていなくても段の降格・復帰と
// 配分の再計算が進む」を担う唯一の駆動体なので、ここが動かなければ機能が無いのと同じ。
// Electron を読み込まずに走ることも合わせて確かめる（読み込んだ瞬間に常駐運用が壊れる）。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const resourceControl = require('../scripts/resource-control');
const budget = require('../src/features/orchestration/main/budget');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function cfgFor(budgetDir, controlDir) {
  return { orchestration: { budgetDir, controlDir } };
}

function writeBudgetConfig(dir, raw) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify(raw, null, 2));
}

// --- 引数 -------------------------------------------------------------------

test('parseArgs はディレクトリ上書きを読み、未知の引数は落とす', () => {
  assert.deepStrictEqual(
    resourceControl.parseArgs(['--control-dir', '/c', '--budget-dir', '/b']),
    { orchestration: { controlDir: '/c', budgetDir: '/b' } }
  );
  assert.deepStrictEqual(resourceControl.parseArgs([]), { orchestration: {} });
  assert.throws(() => resourceControl.parseArgs(['--nope']), /不明な引数/);
});

// --- 再配分の間隔ゲート -----------------------------------------------------

test('auto 配分は未計算なら再配分し、間隔内の再実行では走らない', () => {
  const dir = tmpdir('rc-auto-');
  const control = tmpdir('rc-auto-ctl-');
  writeBudgetConfig(dir, {
    version: 2,
    tokens: 1000,
    allocation: {
      mode: 'auto', rebalance_interval_sec: 600,
      workloads: { flow: { weight: 1 }, project: { weight: 1 } },
    },
  });

  const first = resourceControl.run(cfgFor(dir, control), Date.parse('2020-01-01T00:00:00Z'));
  assert.strictEqual(first.rebalanced, true, '未計算なら 1 回目で再配分する');
  const computedAt = budget.loadBudgetConfig(dir).computed.computed_at;
  assert.ok(computedAt, 'computed_at が刻まれる');

  const soon = resourceControl.run(cfgFor(dir, control), Date.parse(computedAt) + 60 * 1000);
  assert.strictEqual(soon.rebalanced, false, '間隔内は再配分しない');

  const later = resourceControl.run(cfgFor(dir, control), Date.parse(computedAt) + 601 * 1000);
  assert.strictEqual(later.rebalanced, true, '間隔を過ぎたら再配分する');
});

test('manual 配分では一度も再配分しない（人の宣言を上書きしない）', () => {
  const dir = tmpdir('rc-manual-');
  const control = tmpdir('rc-manual-ctl-');
  writeBudgetConfig(dir, {
    version: 2, tokens: 1000,
    allocation: { mode: 'manual', workloads: { flow: { max_tokens: 500 } } },
  });
  const out = resourceControl.run(cfgFor(dir, control), Date.parse('2020-01-01T00:00:00Z'));
  assert.strictEqual(out.rebalanced, false);
  assert.strictEqual(budget.loadBudgetConfig(dir).computed.computed_at, undefined);
});

// --- profiles の適用 --------------------------------------------------------

test('枠を枯らすと Electron 無しで段が降り、control へ書かれる', () => {
  const dir = tmpdir('rc-degrade-');
  const controlDir = tmpdir('rc-degrade-ctl-');
  writeBudgetConfig(dir, {
    version: 2, tokens: 1000,
    allocation: { mode: 'manual', workloads: { flow: { max_tokens: 1000 } } },
  });
  fs.mkdirSync(path.join(dir, 'ledger'), { recursive: true });
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  fs.writeFileSync(path.join(dir, 'ledger', `${day}.jsonl`), `${JSON.stringify({
    ts: new Date().toISOString(), workload: 'flow', tool: 'agent-flow',
    seconds: 1, agent_cli: 'claude', tokens_in: 950, tokens_out: 0,
  })}\n`);

  const profilesDir = path.join(controlDir);
  fs.mkdirSync(profilesDir, { recursive: true });
  fs.writeFileSync(path.join(profilesDir, 'profiles.json'), JSON.stringify({
    version: 1,
    enabled: true,
    tiers: {
      large: { order: 2, candidates: [{ agent_cli: 'claude', model: 'opus' }] },
      small: { order: 1, candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] },
    },
    policy: {
      apply_to: ['flow'], no_cap_tier: 'large', hysteresis: 0, min_hold_sec: 0,
      steps: [{ min_remaining_ratio: 0.5, tier: 'large' }, { min_remaining_ratio: 0, tier: 'small' }],
    },
    state: {},
  }, null, 2));

  const out = resourceControl.run(cfgFor(dir, controlDir), Date.now());
  assert.strictEqual(out.decisions.flow.tier, 'small', '残率 0.05 なら小さい段へ落ちる');
  assert.strictEqual(out.decisions.flow.candidate.agent_cli, 'ollama', 'ローカルへ退避する');
  assert.ok(out.controlWritten, 'control.json へ投函する');
  const control = JSON.parse(fs.readFileSync(path.join(controlDir, 'control.json'), 'utf8'));
  assert.strictEqual(control.workloads.flow.agent_cli, 'ollama');
});

test('ヘッドレス判断は run に渡した時刻で quota の鮮度を評価する', () => {
  const budgetDir = tmpdir('rc-quota-clock-budget-');
  const controlDir = tmpdir('rc-quota-clock-control-');
  writeBudgetConfig(budgetDir, { version: 2, period: 'total', allocation: { mode: 'manual' } });
  fs.mkdirSync(path.join(budgetDir, 'ledger'), { recursive: true });
  fs.writeFileSync(path.join(budgetDir, 'ledger', '20200101.jsonl'), [
    { ts: '2020-01-01T00:04:00Z', event: 'quota_snapshot', agent_cli: 'claude', quota_used_percent: 79 },
    { ts: '2020-01-01T00:04:00Z', event: 'quota_snapshot', agent_cli: 'codex', quota_used_percent: 39 },
  ].map((row) => JSON.stringify(row)).join('\n') + '\n');
  fs.mkdirSync(controlDir, { recursive: true });
  fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
    version: 1, enabled: true,
    tiers: { medium: { order: 2, candidates: [
      { agent_cli: 'claude', model: 'sonnet' }, { agent_cli: 'codex', model: 'gpt-5' },
    ] } },
    policy: { apply_to: ['flow'], steps: [{ min_remaining_ratio: 0, tier: 'medium' }],
              no_cap_tier: 'medium', hysteresis: 0, min_hold_sec: 0, interval_sec: 300 }, state: {},
  }));
  resourceControl.run(cfgFor(budgetDir, controlDir), Date.parse('2020-01-01T00:05:00Z'));
  const control = JSON.parse(fs.readFileSync(path.join(controlDir, 'control.json'), 'utf8'));
  assert.strictEqual(control.workloads.flow.agent_cli, 'codex');
});

test('Electron を読み込まずに動く（常駐運用の前提）', () => {
  assert.ok(!Object.keys(require.cache).some((f) => /[/\\]node_modules[/\\]electron[/\\]/.test(f)),
    'electron が require されている');
});

test('実行するとResource Controllerの鮮度statusを残す', () => {
  const budgetDir = tmpdir('rc-status-budget-');
  const controlDir = tmpdir('rc-status-control-');
  writeBudgetConfig(budgetDir, { version: 2, allocation: { mode: 'auto' } });
  resourceControl.run(cfgFor(budgetDir, controlDir), Date.parse('2020-01-01T00:00:00Z'));
  const status = JSON.parse(fs.readFileSync(
    path.join(controlDir, 'status', 'agent-resource-controller.json'), 'utf8'
  ));
  assert.strictEqual(status.tool, 'agent-resource-controller');
  assert.strictEqual(status.fresh_after_sec, 300);
  assert.strictEqual(status.ts, '2020-01-01T00:00:00.000Z');
});

console.log(`\n${passed} tests passed (resource-control)`);
