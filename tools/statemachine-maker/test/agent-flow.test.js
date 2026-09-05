'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agentFlow = require('../src/main/agent-flow');

function withBus(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-agent-flow-'));
  const previousBus = process.env.AGENT_APP_FLOW_BUS;
  const previousLogs = process.env.AGENT_APP_FLOW_LOGS;
  process.env.AGENT_APP_FLOW_BUS = path.join(base, 'bus');
  process.env.AGENT_APP_FLOW_LOGS = path.join(base, 'logs');
  t.after(() => {
    if (previousBus == null) delete process.env.AGENT_APP_FLOW_BUS; else process.env.AGENT_APP_FLOW_BUS = previousBus;
    if (previousLogs == null) delete process.env.AGENT_APP_FLOW_LOGS; else process.env.AGENT_APP_FLOW_LOGS = previousLogs;
  });
  return { base, bus: process.env.AGENT_APP_FLOW_BUS, logs: process.env.AGENT_APP_FLOW_LOGS };
}

function write(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2));
}

function draft() {
  return { version: 2, id: 'draft', name: '下書き', description: '', purpose: 'implementation', entry: ['one'], exit: ['one'], nodes: [{ id: 'one', label: '実行', kind: 'work', goal: '{{request}} / {{target}}', deps: [], tier: 'auto' }] };
}

test('下書きを inbox に投函し、ログ付きの切り離し実行を開始する', async (t) => {
  const env = withBus(t);
  const calls = [];
  const result = await agentFlow.start({
    source: { type: 'draft', workflow: draft() }, request: '{{target}} を修正', parameters: { target: 'README' }, agent: 'codex', model: 'm', readonly: true,
  }, {
    root: '/repo',
    getContext: async () => ({ agents: ['codex'], defaults: {}, workspace: { ok: false }, tools: { agentFlow: { ok: true } } }),
    startDetached: async (...args) => { calls.push(args); return { pid: 1 }; },
  });
  const inbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${result.runId}.json`), 'utf8'));
  assert.strictEqual(inbox.request, 'README を修正');
  assert.strictEqual(inbox.plan.nodes[0].goal, '{{request}} / README');
  assert.strictEqual(inbox.workspace, null);
  assert.strictEqual(inbox.submitter_context.root, '/repo');
  assert.deepStrictEqual(calls[0][1].slice(0, 7), ['--bus', env.bus, '--run-id', result.runId, '--agent-cli', 'codex', 'run']);
  assert.strictEqual(calls[0][2].logFile, path.join(env.logs, `${result.runId}.log`));
});

test('launching・回答待ち・完了と成果ブランチを bus から合成する', (t) => {
  const { bus } = withBus(t);
  const root = '/repo';
  const launching = 'app-launching';
  write(path.join(bus, 'inbox', `${launching}.json`), { id: launching, request: '依頼', title: '起動', submitter: 'agent-app', submitted_at: new Date().toISOString(), readonly: true, submitter_context: { root, workflow: 'wf', parameters: {} } });
  assert.strictEqual(agentFlow.readRun(root, launching).state, 'launching');

  const runId = 'app-waiting';
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '依頼', submitter: 'agent-app', submitted_at: new Date().toISOString(), submitter_context: { root, workflow: 'wf' } });
  const run = path.join(bus, 'runs', runId);
  write(path.join(run, 'meta.json'), { status: 'running', phase: 'executing', request: '依頼', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), orch_lease_until: Date.now() / 1000 + 60, workspace: { local: root } });
  write(path.join(run, 'graph.json'), { nodes: { one: { goal: '作業', kind: 'work', deps: [] }, approve: { goal: '確認', kind: 'human', deps: ['one'] } } });
  write(path.join(run, 'results', 'one.json'), { status: 'done', output: '成果', data: { publication: { state: 'published', branch: 'af/result', url: 'https://example.test', commit: 'abc' } } });
  write(path.join(run, 'interactions', 'ix-0123456789abcdef', 'request.json'), { node_id: 'approve', mode: 'approval', prompt: '進めますか', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString() });
  const waiting = agentFlow.readRun(root, runId);
  assert.strictEqual(waiting.state, 'waiting');
  assert.strictEqual(waiting.nodes[0].state, 'done');
  assert.strictEqual(waiting.nodes[1].state, 'waiting');
  assert.strictEqual(waiting.delivery.branch, 'af/result');

  write(path.join(run, 'meta.json'), { status: 'done', request: '依頼', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), workspace: { local: root } });
  write(path.join(run, 'final.json'), { finished_at: new Date().toISOString(), summary: '完了' });
  assert.strictEqual(agentFlow.readRun(root, runId).state, 'done');
});

test('人の回答は mode を検証して append-only に保存する', (t) => {
  const { bus } = withBus(t);
  const root = '/repo';
  const runId = 'app-answer';
  const interactionId = 'ix-fedcba9876543210';
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '依頼', submitter: 'agent-app', submitted_at: new Date().toISOString(), submitter_context: { root } });
  write(path.join(bus, 'runs', runId, 'meta.json'), { status: 'running', updated_at: new Date().toISOString(), workspace: { local: root } });
  write(path.join(bus, 'runs', runId, 'interactions', interactionId, 'request.json'), { node_id: 'human', mode: 'choice', prompt: '選択', options: ['A', 'B'], expires_at: new Date(Date.now() + 60000).toISOString() });
  assert.throws(() => agentFlow.respond(root, runId, interactionId, { option: 'C' }), (err) => err.code === 'answer-invalid');
  const result = agentFlow.respond(root, runId, interactionId, { option: 'B' });
  assert.ok(result.responseId);
  const responses = fs.readdirSync(path.join(bus, 'runs', runId, 'interactions', interactionId, 'responses'));
  assert.strictEqual(responses.length, 1);
  assert.strictEqual(result.interaction.state, 'answered');
});

test('agent-flow の成果 JSON を renderer 向けの名前へ揃える', async (t) => {
  const { bus } = withBus(t);
  const root = '/repo';
  const runId = 'app-result';
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '依頼', submitter: 'agent-app', submitted_at: new Date().toISOString(), submitter_context: { root } });
  const found = await agentFlow.result(root, runId, async () => ({
    ok: true,
    stdout: JSON.stringify({ run_id: runId, status: 'done', done: true, request: '依頼', final_nodes: [{ id: 'final', kind: 'synthesize', output: '完了', data: { ok: true }, artifacts: ['a.md'] }] }),
  }));
  assert.strictEqual(found.runId, runId);
  assert.deepStrictEqual(found.finalNodes[0], { id: 'final', kind: 'synthesize', output: '完了', data: { ok: true }, artifacts: ['a.md'] });
});
