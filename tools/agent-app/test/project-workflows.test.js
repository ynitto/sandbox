'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const projects = require('../src/main/projects');
const importer = require('../src/main/projectImport');
const workflows = require('../src/main/projectWorkflows');
const flowStore = require('../src/main/automation/flow-store');
const flowModel = require('../src/main/automation/flow-model');
const agentFlow = require('../src/main/automation/agent-flow');

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'project-workflows-'));
  const kb = path.join(dir, 'kb'), repo = path.join(dir, 'repo'), root = path.join(dir, 'source');
  for (const p of [kb, repo, path.join(root, 'backlog')]) fs.mkdirSync(p, { recursive: true });
  fs.writeFileSync(path.join(root, 'charter.md'), '# 変更前の名前\n## goal\n互換性を維持\n');
  fs.writeFileSync(path.join(root, 'backlog/a.md'), '## T1: API修正\n- desc: APIの例外処理を修正\n- acceptance: 既存の仕様を維持\n- verify: `npm test`\n- status: ready\n');
  const plan = importer.plan({ root, name: '変更後の名前' });
  importer.apply(kb, plan, plan.items.map(item => item.id));
  const context = { ...projects.read(projects.keyOf(kb, plan.folder)), resolved: [{ label: 'repo', role: 'main', path: repo }] };
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return { dir, kb, repo, root, context };
}

test('既存の未完了文書を作業リポジトリのワークフローへ変換し、受入基準と検証を引き継ぐ', t => {
  const { context, repo } = fixture(t);
  const original = fs.readFileSync(path.join(context.kb, 'projects', context.folder, 'pending.md'), 'utf8');
  const items = workflows.list(context);
  assert.equal(items.length, 1);
  assert.equal(items[0].root, repo);
  const converted = workflows.register(context);
  assert.deepEqual(converted.warnings, []);
  assert.equal(converted.registered.length, 1);
  const saved = flowStore.read(repo, items[0].id).workflow;
  assert.equal(saved.name, 'T1: API修正');
  assert.match(saved.description, /変更後の名前/);
  assert.match(saved.defaultRequest, /受入基準: 既存の仕様を維持/);
  assert.match(saved.defaultRequest, /検証: `npm test`/);
  assert.deepEqual(saved.nodes.map(node => [node.kind, node.deps]), [['work', []], ['verify', ['work']]]);
  assert.match(saved.nodes[0].goal, /互換性を維持/);
  assert.equal(flowModel.preview(saved, saved.defaultRequest, {}).ok, true);
  assert.equal(fs.readFileSync(path.join(context.kb, 'projects', context.folder, 'pending.md'), 'utf8'), original);
  assert.equal(fs.existsSync(path.join(repo, '.statemachine')), false, 'agent-appのタスクには変換しない');

  flowStore.save(repo, { ...saved, name: '利用者が編集した名前' });
  workflows.ensure(context, items[0].id);
  assert.equal(flowStore.read(repo, items[0].id).workflow.name, '利用者が編集した名前');
  assert.equal(flowStore.list(repo).length, 1);
});

test('作業先が未設定なら、参照リポジトリやナレッジ保存先に勝手に作らない', t => {
  const { context, kb } = fixture(t);
  context.resolved = [{ label: 'missing', role: 'main', path: '' }, { label: 'reference', role: 'reference', path: kb }];
  const result = workflows.register(context);
  assert.equal(result.registered.length, 0);
  assert.match(result.warnings[0], /フォルダを選んで/);
  assert.equal(flowStore.list(kb).length, 0);
});

test('古い取り込み形式のbacklogも再インポートせず変換できる', t => {
  const { context, root, repo } = fixture(t);
  const base = path.join(context.kb, 'projects', context.folder);
  fs.unlinkSync(path.join(base, 'pending.md'));
  fs.cpSync(path.join(root, 'backlog'), path.join(base, 'backlog'), { recursive: true });
  const result = workflows.register(context);
  assert.equal(result.registered.length, 1);
  assert.match(flowStore.read(repo, result.registered[0].id).workflow.defaultRequest, /検証: `npm test`/);
});

test('backlog.mdの複数タスクは個別に選択してワークフロー化できる', t => {
  const { context, root, kb } = fixture(t);
  fs.writeFileSync(path.join(root, 'backlog.md'), '# Backlog\n\n## T2: 二つ目\n- desc: 処理2\n\n## T3: 三つ目\n- desc: 処理3\n');
  const plan = importer.plan({ root, name: '複数タスク' });
  const item = plan.items.find(item => item.title === 'T3: 三つ目');
  assert.ok(item);
  importer.apply(kb, plan, [item.id]);
  const next = { ...context, ...projects.read(projects.keyOf(kb, plan.folder)) };
  const flows = workflows.list(next);
  assert.deepEqual(flows.map(item => item.name), ['T3: 三つ目']);
  assert.match(flows[0].body, /処理3/);
  assert.doesNotMatch(flows[0].body, /処理2/);
});

test('文中やコードの区切りを別ワークフローにせず、資料の境界で分ける', () => {
  const text = '# 未完了\n\n## 一つ目\n本文\n---\n続き\n```md\n---\n## 例\n```\n出典: backlog/a.md\n\n---\n\n## 二つ目\n本文2\n出典: backlog/b.md';
  const items = workflows.parse(text);
  assert.deepEqual(items.map(item => item.title), ['一つ目', '二つ目']);
  assert.match(items[0].body, /続き/);
  assert.match(items[0].body, /## 例/);
});

test('変換した定義を通常のワークフロー実行経路で投入する', async t => {
  const { context, repo, dir } = fixture(t);
  const [item] = workflows.register(context).registered;
  const definition = flowStore.read(repo, item.id).workflow;
  const oldBus = process.env.AGENT_APP_FLOW_BUS, oldLogs = process.env.AGENT_APP_FLOW_LOGS;
  process.env.AGENT_APP_FLOW_BUS = path.join(dir, 'bus'); process.env.AGENT_APP_FLOW_LOGS = path.join(dir, 'logs');
  t.after(() => {
    if (oldBus === undefined) delete process.env.AGENT_APP_FLOW_BUS; else process.env.AGENT_APP_FLOW_BUS = oldBus;
    if (oldLogs === undefined) delete process.env.AGENT_APP_FLOW_LOGS; else process.env.AGENT_APP_FLOW_LOGS = oldLogs;
  });
  const calls = [];
  const result = await agentFlow.start({ source: { type: 'workflow', id: item.id }, request: definition.defaultRequest, agent: 'test', readonly: true }, {
    root: repo, getContext: async () => ({ agents: ['test'], defaults: {}, workspace: {}, tools: { agentFlow: { ok: true } } }),
    startDetached: async (...args) => { calls.push(args); },
  });
  const inbox = JSON.parse(fs.readFileSync(path.join(dir, 'bus/inbox', `${result.runId}.json`), 'utf8'));
  assert.equal(inbox.submitter_context.workflow, item.id);
  assert.equal(calls[0][0], 'agent-flow');
  assert.equal(calls[0][2].cwd, repo);
  assert.match(inbox.request, /npm test/);
  assert.equal(inbox.plan.nodes[1].kind, 'verify');
});
