'use strict';

// `herd`（agent-herd 一族の 1 語）を会話・タスク・ワークフローで選べることと、選び分けの規則。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');
const herd = require('../src/main/herd');
const agentCli = require('../src/main/agentCli');
const automationIpc = require('../src/main/automation/ipc');

const SRC = path.join(__dirname, '..', 'src');

function family({ aider = true, ollama = true } = {}) {
  return [
    { name: 'claude', command: 'claude', available: true, readonly: 'best-effort', interactive: true },
    { name: 'aider', command: 'agent-herd', available: aider, readonly: 'enforced', interactive: true },
    { name: 'ollama', command: 'agent-herd', available: ollama, readonly: 'enforced', interactive: true },
  ];
}

test('herd: 一族は command[0] が agent-herd の定義から機械的に導く（herd.json は作らない）', () => {
  assert.ok(!fs.existsSync(path.join(process.env.KIRO_AGENTS_DIR, 'herd.json')));
  const defs = agentCli.list('');
  assert.deepStrictEqual(herd.members(defs).map((m) => m.name).sort(), ['aider', 'ollama']);
  const entry = herd.listEntry(defs);
  assert.strictEqual(entry.name, 'herd');
  assert.strictEqual(entry.virtual, true);
  assert.deepStrictEqual(entry.members.sort(), ['aider', 'ollama']);
  assert.strictEqual(entry.readonly, 'enforced', '一族はどちらも readonly を保証する');
  assert.strictEqual(entry.interactive, true, 'tmux の対話起動（共通 TUI）を持つ');
  assert.strictEqual(herd.listEntry([{ name: 'claude', command: 'claude', available: true }]), null, '一族が無ければ出さない');
  assert.strictEqual(herd.isMember(entry), false, '仮想の行は一族の一員として数えない');
});

test('herd: 依頼の形で aider / ollama を選び分ける', () => {
  assert.strictEqual(herd.purposeOf({ readonly: true, workFiles: true }), 'ask');
  assert.strictEqual(herd.purposeOf({ readonly: false, workFiles: true }), 'edit');
  assert.strictEqual(herd.purposeOf({ readonly: false, workFiles: false }), 'work');
  assert.strictEqual(herd.hasWorkFiles([{ id: 'x', name: 'shot.png' }]), false, '写した添付は参考資料');
  assert.strictEqual(herd.hasWorkFiles([{ rel: 'src/a.js', name: 'a.js' }]), true);
  const defs = family();
  assert.strictEqual(herd.resolve('ask', defs).cli, 'ollama');
  assert.strictEqual(herd.resolve('edit', defs).cli, 'aider');
  assert.strictEqual(herd.resolve('work', defs).cli, 'ollama');
  assert.strictEqual(herd.resolve('task', defs).cli, 'aider');
  assert.strictEqual(herd.resolve('plan', defs).cli, 'ollama');
  assert.strictEqual(herd.resolve('unknown', defs).purpose, 'work');
  assert.ok(herd.resolve('edit', defs).reason);
  assert.strictEqual(herd.resolve('edit', defs).fallback, false);
});

test('herd: 使えない一員は飛ばし、一族の外へは倒さない', () => {
  const onlyOllama = family({ aider: false });
  const picked = herd.resolve('edit', onlyOllama);
  assert.strictEqual(picked.cli, 'ollama');
  assert.strictEqual(picked.fallback, true);
  assert.throws(() => herd.resolve('work', family({ aider: false, ollama: false })), /利用できません/);
  assert.throws(() => herd.resolve('work', [{ name: 'claude', command: 'claude', available: true }]), /定義/);
  assert.strictEqual(herd.resolveName('claude', 'work', family()), 'claude');
  assert.strictEqual(herd.resolveName('HERD', 'work', family()), 'ollama');
});

test('herd: 名前の並びには一族が居るときだけ herd を足す', () => {
  const defs = family();
  assert.deepStrictEqual(herd.withVirtualName(['aider', 'claude'], defs), ['aider', 'claude', 'herd']);
  assert.deepStrictEqual(herd.withVirtualName(['claude'], defs), ['claude'], 'agent-herd が解決できない一族は数えない');
  assert.deepStrictEqual(herd.withVirtualName(['aider', 'herd'], defs), ['aider', 'herd']);
});

test('herd: 会話は listAgents に仮想の行を足し、ターンごとに写して要求名を会話に残す', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  assert.match(ipc, /const virtual = herd\.listEntry\(marked\)/);
  assert.match(ipc, /const base = concreteCli\(requested, agents, \{ attachments: p\.attachments \}\)/);
  assert.match(ipc, /cli: base\.requested \|\| base\.cli/, '次のターンの既定は herd のまま（添付の有無で選び直す）');
  assert.match(ipc, /role: 'user', text, cli, family,/);
  assert.match(ipc, /role: 'assistant', cli, family,/);
  assert.match(ipc, /if \(herd\.isHerd\(want\.cli\)\) want = /, '会話を開いただけ・再起動でも herd を写す');
});

test('herd: タスクとワークフローは共有編集面のフックで一覧へ足し、起動前に写す', async () => {
  const makerIpc = fs.readFileSync(path.join(__dirname, '..', '..', 'statemachine-maker', 'src', 'main', 'ipc.js'), 'utf8');
  assert.match(makerIpc, /options\.agentDefinitions/);
  assert.match(makerIpc, /options\.hooks && options\.hooks\.resolveAgent/);
  assert.match(makerIpc, /resolveAgent\(requestedAgent, 'task', root\)/);
  assert.match(makerIpc, /resolveAgent\(requestedAgent, 'plan', root\)/);
  assert.match(makerIpc, /agentFlow\.start\(\{ \.\.\.p, agent \}/, 'agent-flow の --agent-cli には実在の定義名を渡す');
  const adapter = fs.readFileSync(path.join(SRC, 'main/automation/ipc.js'), 'utf8');
  assert.match(adapter, /agentDefinitions,\s*hooks: \{\s*resolveAgent,/);
  const capture = async () => ({ ok: true, stdout: JSON.stringify({ definitions: ['aider', 'claude', 'ollama'] }) });
  assert.deepStrictEqual(await automationIpc.agentDefinitions({ cwd: '', capture }), ['aider', 'claude', 'ollama', 'herd']);
  const none = async () => ({ ok: true, stdout: JSON.stringify({ definitions: ['claude'] }) });
  assert.deepStrictEqual(await automationIpc.agentDefinitions({ cwd: '', capture: none }), ['claude']);
  assert.deepStrictEqual(automationIpc.resolveAgent({ agent: 'claude', purpose: 'task' }), { agent: 'claude' });
  assert.strictEqual(automationIpc.resolveAgent({ agent: 'herd', purpose: 'task' }).agent, 'aider', 'タスク・ワークフローの実行は編集役から');
  assert.strictEqual(automationIpc.resolveAgent({ agent: 'herd', purpose: 'plan' }).agent, 'ollama', 'AI 支援（計画）はツールループから');
});
