'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const allocation = require('../src/shared/allocation');
const settings = require('../src/main/settings');
const store = require('../src/main/store');
const fs = require('fs'), os = require('os'), path = require('path');
const now = Date.parse('2026-09-20T01:00:00Z');
const config = () => settings.normalize({ lastCli: 'claude', allocation: { mode: 'configured', localModel: 'local-model',
  temporary: { mode: 'local', until: '2026-09-20T02:00:00Z' } } });
const agents = [{ name: 'herd', available: true }, { name: 'claude', available: true }];

test('temporary allocation expires without overwriting the normal choice, and explicit requests win', () => {
  const cfg = config();
  assert.equal(settings.resolve(cfg, {}, { agents, now }).cli, 'herd');
  assert.equal(settings.resolve(cfg, {}, { agents, now: now + 3600000 }).cli, 'claude');
  assert.equal(settings.resolve(cfg, { allocation: 'configured' }, { agents, now }).cli, 'claude');
  assert.equal(settings.resolve(cfg, { policy: 'direct', cli: 'codex' }, { agents, now }).cli, 'codex');
  assert.equal(cfg.allocation.mode, 'configured');
  cfg.allocation.temporary.until = null;
  assert.equal(settings.resolve(cfg, {}, { agents, now: now + 999999999 }).cli, 'herd');
});

test('unavailable local preference falls back, local-only stops, and cloud preference selects a configured cloud AI', () => {
  const cfg = config();
  assert.equal(settings.resolve(cfg, {}, { now, agents: agents.slice(1) }).cli, 'claude');
  assert.throws(() => settings.resolve(cfg, { allocation: 'local-only' }, { now, agents: agents.slice(1) }), /利用できません/);
  cfg.execution.tiers.medium.cli = 'herd';
  assert.equal(settings.resolve(cfg, { allocation: 'cloud' }, { now, agents }).cli, 'claude');
  for (const value of Object.values(cfg.execution.tiers)) value.cli = 'herd';
  assert.throws(() => settings.resolve(cfg, { allocation: 'cloud' }, { now, agents }), /クラウド/);
});

test('saving another preference preserves temporary allocation and unrelated configuration', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'allocation-config-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  store.saveConfig(dir, { ...config(), lastModel: 'existing' });
  const saved = store.saveConfig(dir, { notify: { background: false } });
  assert.equal(saved.allocation.temporary.until, '2026-09-20T02:00:00.000Z');
  assert.equal(saved.lastModel, 'existing');
  store.saveConfig(dir, { allocation: { ...saved.allocation, temporary: null } });
  assert.equal(store.loadConfig(dir).allocation.mode, 'configured');
});

test('unknown and expired quotas are never zero; manual input fills missing observations', () => {
  const manual = { agent_cli: 'claude', quota_used_percent: 80, observed_at: '2026-09-20T00:30:00Z', reset_at: '2026-09-20T02:00:00Z' };
  for (const quota_used_percent of [null, undefined, '', -1, 101, 'bad']) assert.equal(allocation.validLimit({ quota_used_percent }, now), false);
  assert.equal(allocation.validLimit({ quota_used_percent: 0 }, now), true);
  assert.equal(allocation.validLimit(manual, now + 3600000), false);
  const automatic = { ...manual, quota_used_percent: 30, quota_source: 'api' };
  assert.equal(allocation.limits([automatic], [manual], now).length, 1);
  assert.equal(allocation.limits([{ ...automatic, quota_used_percent: null }], [manual], now).at(-1).quota_source, 'manual');
  assert.equal(allocation.manualLimits([{ ...manual, agent_cli: '__bad name' }]).length, 0);
});

test('active conversation keeps its AI after a global allocation change', () => {
  const ipc = require('../src/main/ipc');
  const cfg = config(); cfg.allocation.temporary.until = null;
  const sess = { cli: 'claude', model: 'model-a', policy: 'recommended', tier: 'medium' };
  const selected = ipc.executionSpec(sess, { policy: 'recommended', cli: 'claude', prompt: 'continue' }, cfg, { agents });
  assert.equal(selected.cli, 'claude');
  assert.equal(selected.model, 'model-a');
  assert.equal(ipc.executionSpec(sess, { policy: 'direct', cli: 'herd', prompt: 'explicit' }, cfg, { agents }).cli, 'herd');
});
