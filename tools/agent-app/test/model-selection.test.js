'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const selection = require('../src/main/modelSelection');
const settings = require('../src/main/settings');
const store = require('../src/main/store');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const agents = [
  { name: 'claude', available: true }, { name: 'codex', available: false },
  { name: 'ollama', command: 'agent-herd', available: true },
  { name: 'herd', virtual: true, available: true },
];
const config = () => settings.normalize({ allocation: { mode: 'auto', localModel: 'local/model' },
  execution: { tiers: { small: { cli: 'herd', model: 'local/model' }, medium: { cli: 'claude', model: 'cloud' }, large: { cli: 'codex', model: 'large' } } } });
const load = () => ({ defaultModel: 'default' });

test('automatic selection respects explicit and temporary choices', () => {
  const cfg = config();
  assert.equal(settings.resolve(cfg, { policy: 'recommended' }, { agents }).allocation, 'auto');
  assert.equal(settings.resolve(cfg, { policy: 'direct', cli: 'claude' }, { agents }).allocation, undefined);
  cfg.share.enabled = true;
  assert.equal(settings.resolve(cfg, { policy: 'shared' }, { agents }).allocation, undefined);
  cfg.allocation.temporary = { mode: 'local', until: null };
  assert.equal(settings.resolve(cfg, { policy: 'recommended' }, { agents }).cli, 'herd');
  assert.equal(settings.resolve(cfg, { policy: 'recommended', allocation: 'configured' }, { agents }).allocation, undefined);
});

test('candidates are deduplicated concrete definitions, unavailable agents and exhausted manual quotas are removed', () => {
  const cfg = config();
  assert.deepEqual(selection.candidates(cfg, agents, load), [{ cli: 'ollama', model: 'local/model' }, { cli: 'claude', model: 'cloud' }]);
  const limit = { agent_cli: 'claude', quota_used_percent: 100, reset_at: new Date(Date.now() + 60000).toISOString(), observed_at: new Date().toISOString() };
  cfg.audit.manualLimits = [limit];
  assert.equal(selection.candidates(cfg, agents, load).length, 1);
  assert.equal(selection.candidates(cfg, agents, load, { observed: [{ ...limit, quota_used_percent: 10 }] }).length, 2);
  cfg.audit.manualLimits[0].reset_at = '2000-01-01T00:00:00Z';
  assert.equal(selection.candidates(cfg, agents, load).length, 2);
});

for (const stage of ['jev', 'judge', 'audit']) test(`selector uses stdin and accepts a validated ${stage} result`, async () => {
  const prompt = "日本語\n'$(touch /tmp/not-executed)' `hello`";
  const chosen = await selection.select({ config: config(), agents, load, prompt, cwd: '/repo',
    capture: async (name, args, opts) => {
      assert.equal(name, 'agent-herd');
      assert.equal(args.includes(prompt), false);
      assert.equal(opts.input, prompt);
      assert.equal(opts.cwd, '/repo');
      assert.ok(args.includes('ollama/local/model'));
      return { ok: true, stdout: JSON.stringify({ selected: { agent_cli: 'ollama', model: 'local/model' }, stage, state: { secret: 'do not store' } }) };
    } });
  assert.deepEqual(chosen, { cli: 'ollama', model: 'local/model', stage });
  assert.match(selection.information(chosen).title, /ollama/);
});

test('selection fails closed for invalid output, unapproved models, no candidates and cancellation', async () => {
  const base = { config: config(), agents, load, prompt: 'task' };
  for (const result of [
    { ok: false, stdout: '' }, { ok: true, stdout: 'bad json' },
    { ok: true, stdout: JSON.stringify({ stage: 'jev', selected: { agent_cli: 'claude', model: 'not-configured' } }) },
  ]) await assert.rejects(selection.select({ ...base, capture: async () => result }), /自動選択/);
  await assert.rejects(selection.select({ ...base, agents: [] }), /候補/);
  const controller = new AbortController();
  await assert.rejects(selection.select({ ...base, signal: controller.signal, capture: async () => {
    controller.abort(); return { ok: false };
  } }), /停止/);
});

test('only new automatic sessions wait for selection; an actual choice survives reloads', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'selection-session-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const session = store.createSession(dir, { repo: '/repo', cli: 'claude', policy: 'recommended', allocation: 'auto' });
  assert.equal(session.transport, 'headless');
  assert.equal(selection.pending(session), true);
  const choice = { cli: 'ollama', model: 'local/model', stage: 'jev' };
  store.updateSession(dir, session.id, { cli: choice.cli, model: choice.model, modelSelection: choice });
  assert.equal(selection.pending(store.readSession(dir, session.id)), false);
  assert.equal(selection.pending({ messages: [], cli: 'claude' }), false);
  assert.equal(selection.pending({ allocation: 'auto', messages: [{ role: 'user' }] }), false);
});

test('automatically selected local AI retains readonly/edit routing without switching its concrete agent', () => {
  const ipc = require('../src/main/ipc');
  const family = [...agents, { name: 'aider', command: 'agent-herd', available: true }];
  const picked = { cli: 'aider', model: 'chosen', autoSelected: true, readonly: true };
  assert.equal(ipc.concreteCli(picked, family).cli, 'aider');
  assert.equal(ipc.concreteCli(picked, family).slash, '/find');
  assert.equal(ipc.concreteCli({ ...picked, readonly: false }, family, { attachments: [{ rel: 'app.js' }] }).slash, '/edit');
  assert.equal(ipc.concreteCli({ ...picked, autoSelected: false }, family).slash, '');
});
