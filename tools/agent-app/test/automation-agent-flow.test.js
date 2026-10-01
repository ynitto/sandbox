'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const agentFlow = require('../src/main/automation/agent-flow');

function withBus(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-agent-flow-'));
  const previousBus = process.env.AGENT_APP_FLOW_BUS;
  const previousLogs = process.env.AGENT_APP_FLOW_LOGS;
  const previousHome = process.env.AGENT_APP_FLOW_HOME;
  process.env.AGENT_APP_FLOW_BUS = path.join(base, 'bus');
  process.env.AGENT_APP_FLOW_LOGS = path.join(base, 'logs');
  process.env.AGENT_APP_FLOW_HOME = path.join(base, 'home');
  t.after(() => {
    if (previousHome == null) delete process.env.AGENT_APP_FLOW_HOME; else process.env.AGENT_APP_FLOW_HOME = previousHome;
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

test('workspace.local は実行基盤のホスト表記で書き、覚え書きの root は登録した表記のまま残す', async (t) => {
  const env = withBus(t);
  // Windows から WSL の agent-flow を起こす構成の写し: 登録は C:\… で、ホスト（WSL）は /mnt/c/…
  const root = 'C:\\work\\repo';
  const result = await agentFlow.start({
    source: { type: 'draft', workflow: draft() }, request: '{{target}} を修正', parameters: { target: 'README' },
    agent: 'codex', model: 'm', readonly: false,
  }, {
    root,
    hostPath: (value) => (value === root ? '/mnt/c/work/repo' : value),
    getContext: async () => ({
      agents: ['codex'], defaults: {},
      workspace: { ok: true, origin: 'git@example.test:me/repo.git', branch: 'main' },
      tools: { agentFlow: { ok: true } },
    }),
    startDetached: async () => ({ pid: 1 }),
  });
  const inbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${result.runId}.json`), 'utf8'));
  // agent-flow が `git -C` で開く側はホストの表記
  assert.strictEqual(inbox.workspace.local, '/mnt/c/work/repo');
  assert.strictEqual(inbox.workspace.url, 'git@example.test:me/repo.git');
  // この画面が実行を見分ける鍵は登録した表記のまま
  assert.strictEqual(inbox.submitter_context.root, root);
});

test('hostPath を渡さない単体版は、登録した表記のまま workspace.local に書く', async (t) => {
  const env = withBus(t);
  const result = await agentFlow.start({
    source: { type: 'draft', workflow: draft() }, request: '直す', parameters: { target: 'README' }, agent: 'codex', readonly: false,
  }, {
    root: '/repo',
    getContext: async () => ({
      agents: ['codex'], defaults: {},
      workspace: { ok: true, origin: 'git@example.test:me/repo.git', branch: 'main' },
      tools: { agentFlow: { ok: true } },
    }),
    startDetached: async () => ({ pid: 1 }),
  });
  const inbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${result.runId}.json`), 'utf8'));
  assert.strictEqual(inbox.workspace.local, '/repo');
});

test('inbox を持たない実行は、ホスト表記の workspace.local でも選択中のリポジトリのものと見分ける', (t) => {
  const { bus } = withBus(t);
  const root = 'C:\\work\\repo';
  const hostRoot = '/mnt/c/work/repo';
  const runId = 'app-hostlocal';
  // 投函記録が無い実行（外部ツールの投函・掃除された inbox）は meta.workspace.local だけが手掛かり
  write(path.join(bus, 'runs', runId, 'meta.json'), {
    status: 'running', request: '依頼', created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    workspace: { local: hostRoot },
  });
  assert.strictEqual(agentFlow.readRun(root, runId, hostRoot).runId, runId);
  assert.deepStrictEqual(agentFlow.listRuns(root, 30, hostRoot).map((row) => row.runId), [runId]);
  // ホスト表記を渡さなければ従来どおり登録した表記だけで見る（別リポジトリの実行を混ぜない）
  assert.throws(() => agentFlow.readRun(root, runId), (err) => err.code === 'run-not-found');
  assert.deepStrictEqual(agentFlow.listRuns(root, 30), []);
  // 登録した表記で書かれた実行（従来の記録）も引き続き見分ける
  const legacy = 'app-legacylocal';
  write(path.join(bus, 'runs', legacy, 'meta.json'), {
    status: 'running', request: '依頼', created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    workspace: { local: root },
  });
  assert.strictEqual(agentFlow.readRun(root, legacy, hostRoot).runId, legacy);
});

test('定義なしの実行は、規模の目安と計画の確認を inbox で渡す', async (t) => {
  const env = withBus(t);
  const calls = [];
  const deps = {
    root: '/repo',
    getContext: async () => ({ agents: ['codex'], defaults: {}, workspace: { ok: false }, tools: { agentFlow: { ok: true } } }),
    startDetached: async (...args) => { calls.push(args); return { pid: 1 }; },
  };
  const auto = await agentFlow.start({ source: { type: 'auto' }, request: '直す', agent: 'codex', readonly: true, size: 'medium', planGate: true }, deps);
  const inbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${auto.runId}.json`), 'utf8'));
  assert.strictEqual(inbox.size, 'medium');
  assert.strictEqual(inbox.plan_gate, true);
  assert.strictEqual(inbox.plan, undefined);
  // 定義があるときは工程が決まっているので渡さない。未知の規模も渡さない
  const fixed = await agentFlow.start({ source: { type: 'draft', workflow: draft() }, request: '直す', parameters: { target: 'x' }, agent: 'codex', readonly: true, size: 'large', planGate: true }, deps);
  const fixedInbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${fixed.runId}.json`), 'utf8'));
  assert.strictEqual(fixedInbox.size, undefined);
  assert.strictEqual(fixedInbox.plan_gate, undefined);
  const odd = await agentFlow.start({ source: { type: 'auto' }, request: '直す', agent: 'codex', readonly: true, size: 'huge' }, deps);
  assert.strictEqual(JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${odd.runId}.json`), 'utf8')).size, undefined);
  assert.ok(!calls[0][1].includes('--config'));
});

test('ホームの agent-flow.yaml を調整しているときは、その 1 枚を名指しして起動する', async (t) => {
  const env = withBus(t);
  const file = path.join(env.base, 'home', '.agents', 'agent-flow.yaml');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'size: medium\n');
  const calls = [];
  await agentFlow.start({ source: { type: 'auto' }, request: '直す', agent: 'codex', readonly: true }, {
    root: path.join(env.base, 'repo'),
    getContext: async () => ({ agents: ['codex'], defaults: {}, workspace: { ok: false }, tools: { agentFlow: { ok: true } } }),
    startDetached: async (...args) => { calls.push(args); return { pid: 1 }; },
    hostPath: (value) => `host:${value}`,
  });
  const args = calls[0][1];
  assert.strictEqual(args[args.indexOf('--config') + 1], `host:${file}`);
  assert.ok(args.indexOf('--config') < args.indexOf('run'));
});

test('実行した工程をワークフローの下書きにする（差し込まれた工程は落として依存をつなぐ）', (t) => {
  const { bus } = withBus(t);
  const root = '/repo';
  const runId = 'app-plan';
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, title: 'README を直す', request: 'README を直す', submitter: 'agent-app', submitted_at: new Date().toISOString(), submitter_context: { root } });
  write(path.join(bus, 'runs', runId, 'meta.json'), { status: 'done', created_at: new Date().toISOString() });
  write(path.join(bus, 'runs', runId, 'graph.json'), { nodes: {
    'plan-gate': { id: 'plan-gate', kind: 'human', goal: '計画の確認', deps: [] },
    'base-sync-1': { id: 'base-sync-1', kind: 'base-sync', goal: '同期', deps: ['plan-gate'] },
    t1: { id: 't1', kind: 'work', goal: '[scope] README.md\n[out_of_scope] なし\nREADME の誤記を直す', deps: ['base-sync-1'] },
    v: { id: 'v', kind: 'verify', goal: '直したことを確かめる', deps: ['t1'] },
    'v-m1': { id: 'v-m1', kind: 'map', goal: '展開', deps: ['v'], dynamic: true },
  } });
  const workflow = agentFlow.planDraft(root, runId);
  assert.deepStrictEqual(workflow.nodes.map((node) => node.id), ['t1', 'v']);
  assert.deepStrictEqual(workflow.nodes[0].deps, []);
  assert.deepStrictEqual(workflow.nodes[1].deps, ['t1']);
  assert.strictEqual(workflow.nodes[0].label, 'README の誤記を直す');
  assert.strictEqual(workflow.name, 'README を直す');
  assert.strictEqual(workflow.defaultRequest, 'README を直す');
  const checked = require('../src/main/automation/flow-model').preview(workflow, workflow.defaultRequest, {});
  assert.ok(checked.ok, JSON.stringify(checked.issues));
});

test('分担と確認: 置き換えられた工程も数え、確認の合否の並び・作り直し・候補の採否を返す', (t) => {
  const { bus } = withBus(t);
  const root = '/repo';
  const runId = 'app-teamwork';
  const now = new Date().toISOString();
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '直して確かめる', submitter: 'agent-app', submitted_at: now, submitter_context: { root } });
  const run = path.join(bus, 'runs', runId);
  write(path.join(run, 'meta.json'), { status: 'running', phase: 'executing', request: '依頼', created_at: now, updated_at: now, orch_lease_until: Date.now() / 1000 + 60 });
  // 差し戻しで build / check は build-r1 / check-r1 に置き換わっている（旧ノードはグラフから消え、結果だけが残る）
  write(path.join(run, 'graph.json'), { nodes: {
    a: { goal: '案 A', kind: 'generate', deps: [] }, b: { goal: '案 B', kind: 'generate', deps: [] },
    pick: { goal: '選ぶ', kind: 'judge', deps: ['a', 'b'] },
    'build-r1': { goal: '作る', kind: 'work', deps: ['pick'] }, 'check-r1': { goal: '確かめる', kind: 'verify', deps: ['build-r1'] },
  } });
  const result = (id, kind, extra) => write(path.join(run, 'results', `${id}.json`), { id, kind, status: 'done', output: '', ...extra });
  result('a', 'generate', { agent_cli: 'herd', finished_at: '2026-09-27T00:00:01Z' });
  result('b', 'generate', { agent_cli: 'herd', finished_at: '2026-09-27T00:00:02Z' });
  result('pick', 'judge', { agent_cli: 'herd', finished_at: '2026-09-27T00:00:03Z', data: { winner: 'a', decided_by: 'machine', kept: ['a'] } });
  result('build', 'work', { agent_cli: 'codex', model: 'm1', finished_at: '2026-09-27T00:00:04Z' });
  result('check', 'verify', { agent_cli: 'claude', finished_at: '2026-09-27T00:00:05Z', data: { ok: false } });
  result('build-r1', 'work', { agent_cli: 'codex', model: 'm1', finished_at: '2026-09-27T00:00:06Z' });
  result('check-r1', 'verify', { agent_cli: 'claude', finished_at: '2026-09-27T00:00:07Z', output: 'verify=pass' });
  fs.mkdirSync(path.join(run, 'events'), { recursive: true });
  fs.writeFileSync(path.join(run, 'events', 'orch.jsonl'), [
    { kind: 'evaluate', decision: 'replan' },
    { kind: 'replan', changes: { replaced: [{ old: 'build', next: 'build-r1' }, { old: 'check', next: 'check-r1' }] } },
    { kind: 'replan', changes: { replaced: [] } },
  ].map((row) => JSON.stringify(row)).join('\n') + '\n');

  assert.strictEqual(agentFlow.readRun(root, runId).teamwork, null, '実行中は数えない');

  write(path.join(run, 'meta.json'), { status: 'done', request: '依頼', created_at: now, updated_at: now });
  write(path.join(run, 'final.json'), { finished_at: now, verification: { state: 'passed' } });
  const tw = agentFlow.readRun(root, runId).teamwork;
  assert.deepStrictEqual(tw.roles.map((row) => row.role), ['make', 'compare', 'check']);
  assert.deepStrictEqual(tw.roles[0], { role: 'make', agents: ['herd', 'codex / m1'], attempts: 4, verdicts: [] });
  assert.deepStrictEqual(tw.roles[2].verdicts, ['fail', 'pass']);
  assert.deepStrictEqual(tw.roles[2].agents, ['claude']);
  assert.strictEqual(tw.agents, 3);
  assert.strictEqual(tw.reworks, 1, '置き換えの無い再計画は作り直しに数えない');
  assert.deepStrictEqual(tw.choices, [{ nodeId: 'pick', kind: 'judge', candidates: 2, kept: 1, decidedBy: 'machine', undecided: 0 }]);
  assert.strictEqual(tw.verification, 'passed');
});

test('分担と確認: 判定の無い確認の出力は不合格として数える（エンジンの完了条件と同じ）', (t) => {
  const { bus } = withBus(t);
  const runId = 'app-teamwork-ambiguous';
  const now = new Date().toISOString();
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '依頼', submitter: 'agent-app', submitted_at: now, submitter_context: { root: '/repo' } });
  const run = path.join(bus, 'runs', runId);
  write(path.join(run, 'meta.json'), { status: 'failed', request: '依頼', created_at: now, updated_at: now });
  write(path.join(run, 'graph.json'), { nodes: { v: { goal: '確かめる', kind: 'verify', deps: [] } } });
  write(path.join(run, 'results', 'v.json'), { id: 'v', status: 'done', output: 'よさそうです' });
  const tw = agentFlow.readRun('/repo', runId).teamwork;
  assert.deepStrictEqual(tw.roles, [{ role: 'check', agents: [], attempts: 1, verdicts: ['fail'] }]);
  assert.strictEqual(tw.reworks, 0);
});

test('依頼から実行で選んだ分担の形は、agent-flow の標準パターンとして inbox に名指しする', async (t) => {
  const env = withBus(t);
  const shapes = require('../src/shared/flowShapes');
  const deps = {
    root: '/repo',
    getContext: async () => ({ agents: ['codex'], defaults: {}, workspace: { ok: false }, tools: { agentFlow: { ok: true } } }),
    startDetached: async () => ({ pid: 1 }),
  };
  const verify = shapes.find('verify');
  const started = await agentFlow.start({ source: { type: 'pattern', pattern: verify.pattern }, request: '不具合を直して確かめる', agent: 'codex', readonly: true, size: 'small', planGate: true }, deps);
  const inbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${started.runId}.json`), 'utf8'));
  assert.strictEqual(inbox.pattern, 'adversarial-verification');
  assert.strictEqual(inbox.plan, undefined, '定義（plan）とは同時に渡さない（agent-flow が拒む組み合わせ）');
  assert.strictEqual(inbox.submitter_context.source, 'pattern');
  // 再実行で形を戻せるよう、読み出しにも残る
  assert.strictEqual(agentFlow.readRun('/repo', started.runId).input.pattern, 'adversarial-verification');

  const auto = await agentFlow.start({ source: { type: 'auto' }, request: 'おまかせ', agent: 'codex', readonly: true }, deps);
  const autoInbox = JSON.parse(fs.readFileSync(path.join(env.bus, 'inbox', `${auto.runId}.json`), 'utf8'));
  assert.strictEqual(autoInbox.pattern, undefined, 'おまかせは名指ししない（planner が決める）');
});

// 失敗した実行: どの工程で・どの種類の失敗か、後ろの工程は未実行、続きから再実行は同じ run-id で起こす
function failedRun(bus, logs, runId, root, output, data) {
  write(path.join(bus, 'inbox', `${runId}.json`), { id: runId, request: '依頼', submitter: 'agent-app', submitted_at: '2026-09-30T09:00:00Z', submitter_context: { root, workflow: 'wf', agent: 'codex', model: 'gpt-test' } });
  const run = path.join(bus, 'runs', runId);
  write(path.join(run, 'meta.json'), { status: 'failed', request: '依頼', created_at: '2026-09-30T09:00:00Z', updated_at: '2026-09-30T09:01:00Z', workspace: { local: root } });
  write(path.join(run, 'graph.json'), { nodes: {
    make: { goal: '作る', label: '作る工程', kind: 'work', deps: [] },
    check: { goal: '確かめる', kind: 'verify', deps: ['make'] },
    report: { goal: 'まとめる', kind: 'synthesize', deps: ['check'] },
  } });
  write(path.join(run, 'results', 'make.json'), { status: 'done', output: '作りました', who: 'pc-a/w1' });
  write(path.join(run, 'results', 'check.json'), { status: 'failed', output, data, who: 'pc-b/w1' });
  return run;
}

test('失敗した工程の理由と種類・未実行の工程・履歴の手掛かりを返す', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  failedRun(bus, logs, 'app-failed', root, 'verify=fail: CHANGELOG.md がありません\n参照した場所: docs/', { ok: false, error_class: 'content' });
  const run = agentFlow.readRun(root, 'app-failed');
  assert.strictEqual(run.state, 'failed');
  assert.strictEqual(run.nodes[0].label, '作る工程');
  assert.deepStrictEqual(run.nodes[1].error, { cls: 'content', group: 'content', message: 'CHANGELOG.md がありません', remedy: '' });
  assert.strictEqual(run.nodes[2].state, 'skipped', '前の工程が失敗して動かなかった工程は未実行（回答待ちではない）');
  const row = agentFlow.listRuns(root).find((item) => item.runId === 'app-failed');
  assert.deepStrictEqual(row.failedNode, { id: 'check', label: '', cls: 'content', message: 'CHANGELOG.md がありません' });

  failedRun(bus, logs, 'app-auth', root, '[agent-error:auth] claude 失敗 (rc=1): 認証に失敗しています（再ログインが必要です）\nnot authenticated', {});
  const auth = agentFlow.readRun(root, 'app-auth').nodes[1].error;
  assert.deepStrictEqual(auth, { cls: 'auth', group: 'setup', message: '認証に失敗しています（再ログインが必要です）', remedy: 'AI にログインし直してから再実行してください' });
});

// 直すまで同じ失敗になるもの（setup）は、直し方の 1 行を main が分類と一緒に返す。画面はエラー文から推し量らない。
test('認証・環境・上限・停止の指示の失敗には、直し方の 1 行が付く（一時的な失敗・中身の失敗には付かない）', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  const cases = {
    auth: 'ログインし直して', env: '接続先に届くか', quota: 'レート制限', control: '止める指示',
    transient: '', integration: '', content: '',
  };
  for (const [cls, hint] of Object.entries(cases)) {
    failedRun(bus, logs, `app-${cls}`, root, `[agent-error:${cls}] 失敗しました`, { error_class: cls });
    const error = agentFlow.readRun(root, `app-${cls}`).nodes[1].error;
    assert.strictEqual(error.cls, cls);
    if (hint) {
      assert.strictEqual(error.group, 'setup', cls);
      assert.ok(error.remedy.includes(hint), `${cls}: ${error.remedy}`);
      assert.ok(!/agent-error|error_class|control\b/.test(error.remedy), `${cls}: 内部の綴りを出さない`);
    } else {
      assert.notStrictEqual(error.group, 'setup', cls);
      assert.strictEqual(error.remedy, '', cls);
    }
  }
});

test('続きから再実行は失敗した実行を同じ run-id で起こし、消える前の失敗を控える', async (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  failedRun(bus, logs, 'app-resume', root, 'verify=fail: 足りません', { error_class: 'content' });
  const calls = [];
  const deps = {
    root,
    getContext: async () => ({ agents: ['codex'], defaults: {}, workspace: { ok: true }, tools: { agentFlow: { ok: true } } }),
    startDetached: async (...args) => { calls.push(args); return { pid: 1 }; },
  };
  const started = await agentFlow.resume(root, 'app-resume', deps);
  assert.strictEqual(started.runId, 'app-resume');
  assert.deepStrictEqual(calls[0][1], ['--bus', bus, '--run-id', 'app-resume', '--agent-cli', 'codex', 'run', '--model', 'gpt-test']);
  assert.strictEqual(calls[0][2].logFile, path.join(logs, 'app-resume.log'), '同じ実行ログへ書き足す');
  const attempts = agentFlow.readRun(root, 'app-resume').attempts;
  assert.strictEqual(attempts.length, 1);
  assert.deepStrictEqual({ nodeId: attempts[0].nodeId, cls: attempts[0].cls, message: attempts[0].message }, { nodeId: 'check', cls: 'content', message: '足りません' });

  write(path.join(bus, 'runs', 'app-resume', 'meta.json'), { status: 'done', request: '依頼', created_at: '2026-09-30T09:00:00Z', workspace: { local: root } });
  await assert.rejects(() => agentFlow.resume(root, 'app-resume', deps), /失敗した実行だけ/);
});

test('工程のセッションログは担当の行を受け持ってから次の工程までと、その工程の出来事で組む', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  const run = failedRun(bus, logs, 'app-log', root, 'verify=fail: 足りません', {});
  fs.mkdirSync(logs, { recursive: true });
  fs.writeFileSync(path.join(logs, 'app-log.log'), [
    '[2026-09-30T09:00:01Z] [pc-a/w1] claim 成功: make [work] — 作る',
    '[2026-09-30T09:00:02Z] [pc-b/w1] claim 成功: check [verify] — 確かめる',
    '[2026-09-30T09:00:03Z] [pc-b/w1] 検証しています',
    '  続きの行',
    '[2026-09-30T09:00:04Z] [pc-a/w1] 作っています',
    '[2026-09-30T09:00:06Z] [pc-b/w1] claim 成功: report [synthesize] — まとめる',
    '[2026-09-30T09:00:07Z] [pc-b/w1] まとめています',
  ].join('\n'));
  fs.mkdirSync(path.join(run, 'events'), { recursive: true });
  fs.writeFileSync(path.join(run, 'events', 'pc-b-w1.jsonl'), `${JSON.stringify({ ts: '2026-09-30T09:00:05Z', who: 'pc-b/w1', kind: 'result', node: 'check', status: 'failed' })}\n`);
  const found = agentFlow.readNodeLog(root, 'app-log', 'check');
  assert.deepStrictEqual(found.text.split('\n'), [
    '[2026-09-30T09:00:02Z] [pc-b/w1] claim 成功: check [verify] — 確かめる',
    '[2026-09-30T09:00:03Z] [pc-b/w1] 検証しています',
    '  続きの行',
    '[2026-09-30T09:00:05Z] [pc-b/w1] result status=failed',
  ]);
});

// 実行ログが末尾の読み取り幅（1MiB）より長いとき: 担当の claim が幅の手前にあっても、幅の中の続きの行を
// その工程のものとして返し、始まりが見えていないことを明示する。他の工程・他の担当の行は混ぜない。
test('工程のセッションログは、受け持ちの行が読み取り幅の手前にあっても続きを返し、前半の省略を明示する', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  const run = failedRun(bus, logs, 'app-biglog', root, 'verify=fail: 足りません', {});
  fs.mkdirSync(logs, { recursive: true });
  const pad = (i) => `[2026-09-30T09:00:10Z] [pc-a/w1] 作っています ${String(i).padStart(6, '0')} ${'x'.repeat(80)}`;
  const head = '[2026-09-30T09:00:02Z] [pc-b/w1] claim 成功: check [verify] — 確かめる\n';
  const padding = `${Array.from({ length: 26000 }, (_v, i) => pad(i)).join('\n')}\n`;
  const tailLines = [
    '[2026-09-30T09:00:20Z] [pc-b/w1] 検証の続き（末尾）',
    '  続きの行',
    '[2026-09-30T09:00:21Z] [pc-a/w1] 作り終えました',
    '[2026-09-30T09:00:22Z] [pc-b/w1] claim 成功: report [synthesize] — まとめる',
    '[2026-09-30T09:00:23Z] [pc-b/w1] まとめています',
  ].join('\n');
  const file = path.join(logs, 'app-biglog.log');
  fs.writeFileSync(file, head + padding + tailLines);
  assert.ok(fs.statSync(file).size > 2 * 1024 * 1024, '読み取り幅（1MiB）の 2 倍を超え、遡りは塊の境目をまたぐ');

  const found = agentFlow.readNodeLog(root, 'app-biglog', 'check');
  assert.strictEqual(found.truncated, true);
  assert.strictEqual(found.headOmitted, true, '始まり（claim の行）は返せないので省略を明示する');
  assert.deepStrictEqual(found.text.split('\n'), ['[2026-09-30T09:00:20Z] [pc-b/w1] 検証の続き（末尾）', '  続きの行']);

  // 担当 pc-a の工程: 受け持ちの行は読み切れない（遡っても claim が無い）。空欄にせず省略を明示し、他の担当の行は混ぜない
  const make = agentFlow.readNodeLog(root, 'app-biglog', 'make');
  assert.strictEqual(make.headOmitted, true);
  assert.ok(!/pc-b\/w1/.test(make.text), make.text.slice(0, 200));
});

test('工程のセッションログは、幅の中で受け持ちが始まる工程を省略扱いにせず、手前の工程の行を混ぜない', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  failedRun(bus, logs, 'app-biglog2', root, 'verify=fail: 足りません', {});
  fs.mkdirSync(logs, { recursive: true });
  const padding = `${Array.from({ length: 13000 }, (_v, i) => `[2026-09-30T09:00:01Z] [pc-b/w1] 前の工程の行 ${i} ${'y'.repeat(80)}`).join('\n')}\n`;
  fs.writeFileSync(path.join(logs, 'app-biglog2.log'), '[2026-09-30T09:00:00Z] [pc-b/w1] claim 成功: make [work] — 作る\n' + padding + [
    '[2026-09-30T09:00:30Z] [pc-b/w1] claim 成功: check [verify] — 確かめる',
    '[2026-09-30T09:00:31Z] [pc-b/w1] 検証しています',
  ].join('\n'));
  const found = agentFlow.readNodeLog(root, 'app-biglog2', 'check');
  assert.strictEqual(found.truncated, true);
  assert.strictEqual(found.headOmitted, false);
  assert.deepStrictEqual(found.text.split('\n'), [
    '[2026-09-30T09:00:30Z] [pc-b/w1] claim 成功: check [verify] — 確かめる',
    '[2026-09-30T09:00:31Z] [pc-b/w1] 検証しています',
  ]);
});

test('実行を削除すると、続きから再実行が控えた失敗の履歴も含め、その実行のファイルが残らない', (t) => {
  const { bus, logs } = withBus(t);
  const root = '/repo';
  for (const id of ['app-del', 'app-del-plain']) {
    failedRun(bus, logs, id, root, 'verify=fail: 足りません', {});
    fs.mkdirSync(logs, { recursive: true });
    fs.writeFileSync(path.join(logs, `${id}.log`), '[2026-09-30T09:00:00Z] [pc-a/w1] 作っています\n');
    fs.mkdirSync(path.join(bus, 'inbox', 'claims', id), { recursive: true });
    write(path.join(bus, 'inbox', 'cancels', `${id}.json`), { id });
  }
  write(path.join(logs, 'app-del.attempts.json'), [{ at: '2026-09-30T09:02:00Z', nodeId: 'check', cls: 'content', message: '足りません' }]);
  assert.strictEqual(agentFlow.readRun(root, 'app-del').attempts.length, 1);

  assert.deepStrictEqual(agentFlow.deleteRun(root, 'app-del'), { deleted: true });
  assert.deepStrictEqual(agentFlow.deleteRun(root, 'app-del-plain'), { deleted: true }, '控えが無くてもエラーにしない');
  const left = [];
  const walk = (dir) => { for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) { const p = path.join(dir, name); left.push(p); if (fs.statSync(p).isDirectory()) walk(p); } };
  walk(bus); walk(logs);
  assert.deepStrictEqual(left.filter((p) => /app-del/.test(path.basename(p))), [], '実行由来のファイル・フォルダが残らない');
});
