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

test('herd: 会話は共通 TUI を 1 本開き、用途はスラッシュ行で表す（CLI を入れ替えない）', () => {
  assert.strictEqual(herd.purposeOf({ readonly: true, workFiles: true }), 'ask');
  assert.strictEqual(herd.purposeOf({ readonly: false, workFiles: true }), 'edit');
  assert.strictEqual(herd.purposeOf({ readonly: false, workFiles: false }), 'work');
  assert.strictEqual(herd.hasWorkFiles([{ id: 'x', name: 'shot.png' }]), false, '写した添付は参考資料');
  assert.strictEqual(herd.hasWorkFiles([{ rel: 'src/a.js', name: 'a.js' }]), true);
  const defs = family();
  for (const purpose of ['ask', 'edit', 'work', 'unknown']) {
    assert.strictEqual(herd.resolveChat(purpose, defs).cli, 'ollama', `${purpose}: 起動するのは agent-herd の既定バックエンド`);
  }
  assert.strictEqual(herd.resolveChat('ask', defs).slash, '/find');
  assert.strictEqual(herd.resolveChat('edit', defs).slash, '/edit');
  assert.strictEqual(herd.resolveChat('work', defs).slash, '');
  assert.strictEqual(herd.resolveChat('unknown', defs).purpose, 'work');
  assert.ok(herd.resolveChat('edit', defs).reason);
  assert.strictEqual(herd.withSlash('/edit', 'この関数を直して'), '/edit\nこの関数を直して', 'スラッシュ行は本文の先頭');
  assert.strictEqual(herd.withSlash('', 'そのまま'), 'そのまま');
});

test('herd: 一族の外へは倒さない。既定バックエンドが無ければ一族の他の定義（同じ共通 TUI）', () => {
  assert.strictEqual(herd.resolveChat('work', family({ ollama: false })).cli, 'aider');
  assert.throws(() => herd.resolveChat('work', family({ aider: false, ollama: false })), /利用できません/);
  assert.throws(() => herd.resolveChat('work', [{ name: 'claude', command: 'claude', available: true }]), /定義/);
});

test('herd: タスクと AI 支援は名前を渡さず agent-herd の既定に任せ、agent-flow だけ harness の既定を渡す', () => {
  assert.deepStrictEqual(herd.resolveAutomation('task').agent, '');
  assert.deepStrictEqual(herd.resolveAutomation('plan').agent, '');
  assert.deepStrictEqual(herd.resolveAutomation('flow').agent, herd.HARNESS_DEFAULT);
  assert.strictEqual(herd.HARNESS_DEFAULT, 'aider');
  assert.strictEqual(herd.CHAT_BACKEND, 'ollama');
});

test('herd: 名前の並びには一族が居るときだけ herd を足す', () => {
  const defs = family();
  assert.deepStrictEqual(herd.withVirtualName(['aider', 'claude'], defs), ['aider', 'claude', 'herd']);
  assert.deepStrictEqual(herd.withVirtualName(['claude'], defs), ['claude'], 'agent-herd が解決できない一族は数えない');
  assert.deepStrictEqual(herd.withVirtualName(['aider', 'herd'], defs), ['aider', 'herd']);
});

test('herd: 会話は listAgents に仮想の行を足し、ターンごとにスラッシュ行を付けて要求名を会話に残す', () => {
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  assert.match(ipc, /const virtual = herd\.listEntry\(marked\)/);
  assert.match(ipc, /const base = concreteCli\(requested, agents, \{ attachments: p\.attachments \}\)/);
  assert.match(ipc, /cli: base\.requested \|\| base\.cli/, '次のターンの既定は herd のまま');
  assert.match(ipc, /role: 'user', text, cli, family,/);
  assert.match(ipc, /role: 'assistant', cli, family,/);
  assert.match(ipc, /herd\.withSlash\(slash, turn\.prompt\)/, 'ヘッドレスでも本文の先頭にスラッシュ行');
  assert.match(ipc, /herd\.withSlash\(slash, unseen\.length \? agentCli\.replayPrompt/, 'tmux では履歴の再送より前にスラッシュ行');
  assert.match(ipc, /if \(herd\.isHerd\(want\.cli\)\) want = /, '会話を開いただけ・再起動でも共通 TUI を開く');
});

test('herd: タスクとワークフローは共有編集面のフックで一覧へ足し、起動前に写す', async () => {
  const makerIpc = fs.readFileSync(path.join(__dirname, '..', '..', 'statemachine-maker', 'src', 'main', 'ipc.js'), 'utf8');
  const makerTools = fs.readFileSync(path.join(__dirname, '..', '..', 'statemachine-maker', 'src', 'main', 'tools.js'), 'utf8');
  assert.match(makerIpc, /options\.agentDefinitions/);
  assert.match(makerIpc, /options\.hooks && options\.hooks\.resolveAgent/);
  assert.match(makerIpc, /resolveAgent\(requestedAgent, 'task', root\)/);
  assert.match(makerIpc, /resolveAgent\(requestedAgent, 'plan', root\)/);
  assert.match(makerIpc, /resolveAgent\(requestedAgent, 'flow', root\)/);
  assert.match(makerIpc, /agentFlow\.start\(\{ \.\.\.p, agent \}/, 'agent-flow の --agent-cli には実在の定義名を渡す');
  assert.match(makerTools, /\.\.\.\(agent \? \['--agent', String\(agent\)\] : \[\]\)/, 'AI 支援は agent が空なら --agent を渡さない');
  const adapter = fs.readFileSync(path.join(SRC, 'main/automation/ipc.js'), 'utf8');
  // 共有編集面へ渡す配線。順番や隣接ではなく、項目ごとに見る（項目が増えても壊れない）
  assert.match(adapter, /makerIpc\.registerIpcHandlers\(getWindow, \{[\s\S]*\n  \}\);/);
  assert.match(adapter, /^\s*agentDefinitions,$/m);
  assert.match(adapter, /^\s*commandSpawnSpec: makeTaskCommandSpawnSpec\(userData\),$/m);
  assert.match(adapter, /^\s*hooks: \{\s*$[\s\S]*^\s*resolveAgent,$/m);
  assert.match(adapter, /agentCli\.load\(agent \|\| herd\.HARNESS_DEFAULT, root\)/);
  const capture = async () => ({ ok: true, stdout: JSON.stringify({ definitions: ['aider', 'claude', 'ollama'] }) });
  assert.deepStrictEqual(await automationIpc.agentDefinitions({ cwd: '', capture }), ['aider', 'claude', 'ollama', 'herd']);
  const none = async () => ({ ok: true, stdout: JSON.stringify({ definitions: ['claude'] }) });
  assert.deepStrictEqual(await automationIpc.agentDefinitions({ cwd: '', capture: none }), ['claude']);
  assert.deepStrictEqual(automationIpc.resolveAgent({ agent: 'claude', purpose: 'task' }), { agent: 'claude' });
  assert.strictEqual(automationIpc.resolveAgent({ agent: 'herd', purpose: 'task' }).agent, '', 'タスクは --agent-cli を渡さない');
  assert.strictEqual(automationIpc.resolveAgent({ agent: 'herd', purpose: 'plan' }).agent, '', 'AI 支援は --agent を渡さない');
  assert.strictEqual(automationIpc.resolveAgent({ agent: 'herd', purpose: 'flow' }).agent, 'aider', 'agent-flow には harness の既定');
});
