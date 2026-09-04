'use strict';

// Electron を起動せずに固定する: 構文・argv の組み立て・セッションの保存・git の読み取り。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');
const agentCli = require('../src/main/agentCli');
const store = require('../src/main/store');
const git = require('../src/main/git');

const SRC = path.join(__dirname, '..', 'src');

test('main / ipc / preload / renderer は構文検査を通る', () => {
  for (const f of ['main/main.js', 'main/ipc.js', 'main/agentCli.js', 'main/store.js', 'main/git.js', 'preload.js', 'renderer/renderer.js']) {
    execFileSync(process.execPath, ['--check', path.join(SRC, f)]);
  }
  const main = fs.readFileSync(path.join(SRC, 'main/main.js'), 'utf8');
  assert.ok(main.includes('contextIsolation: true') && main.includes('sandbox: true'));
  assert.ok(fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8').includes("script-src 'self'"));
});

test('preload の窓口と ipc のチャネルが 1 対 1', () => {
  const pre = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  const invoked = [...pre.matchAll(/invoke\('([\w:]+)'/g)].map((m) => m[1]);
  const handled = [...ipc.matchAll(/handle\('([\w:]+)'/g)].map((m) => m[1]);
  assert.deepStrictEqual([...new Set(invoked)].sort(), handled.sort());
});

// 同梱定義から出る argv。権限フラグと prompt の渡し方は agent-dashboard のゴールデンと同じ。
test('argv: 初回ターン（write / readonly）', () => {
  const uuid = /^[0-9a-f-]{36}$/;
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'P', model: 'M' });
  assert.match(claude.mintedSession, uuid);
  assert.deepStrictEqual(claude.argv, ['claude', '--session-id', claude.mintedSession, '-p', '--output-format', 'text',
    '--dangerously-skip-permissions', '--model', 'M']);
  assert.strictEqual(claude.stdin, 'P');

  const copilot = agentCli.turnCmd(agentCli.load('copilot'), { prompt: 'P', readonly: true });
  assert.deepStrictEqual(copilot.argv, ['copilot', '--session-id', copilot.mintedSession, '-s', '--allow-all-tools', '--no-color',
    '--available-tools=view,grep,glob', '--disable-builtin-mcps', '--no-custom-instructions', '-p', 'P']);
  assert.ok(copilot.readonlyWarning, 'best-effort の CLI には警告が付く');

  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'P' });
  assert.deepStrictEqual(codex.argv.slice(0, 2), ['codex', 'exec']);
  assert.ok(codex.argv.includes('--json') && codex.argv.at(-1) === '-' && codex.outputFile);
  assert.ok(codex.argv.includes(codex.outputFile));

  const kiro = agentCli.turnCmd(agentCli.load('kiro'), { prompt: 'P', model: 'M' });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'M', 'P']);
  assert.ok(kiro.listArgs);
});

test('argv: 継続ターンは resume をサブコマンド直後に差し込む', () => {
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'P', cliSession: 'S' });
  assert.deepStrictEqual(claude.argv, ['claude', '--resume', 'S', '-p', '--output-format', 'text', '--dangerously-skip-permissions']);
  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'P', cliSession: 'T' });
  assert.deepStrictEqual(codex.argv.slice(0, 4), ['codex', 'exec', 'resume', 'T']);
  const kiro = agentCli.turnCmd(agentCli.load('kiro'), { prompt: 'P', cliSession: 'K', readonly: true });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--resume-id', 'K', '--no-interactive', '--trust-tools=fs_read', 'P']);
});

test('argv: セッション機能の無い CLI は履歴を再送する', () => {
  const history = [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }];
  const t = agentCli.turnCmd(agentCli.load('vscode-copilot'), { prompt: 'c', history });
  assert.ok(t.stdin.includes('[user] a') && t.stdin.includes('[assistant] b') && t.stdin.endsWith('新しい依頼:\nc'));
  const cursor = agentCli.turnCmd(agentCli.load('cursor'), { prompt: 'c', history });
  assert.ok(cursor.argv.includes('--continue'), 'continue_args を持つ CLI はそれを使う');
});

test('応答から端末の装飾と kiro の入力欄を剥がす', () => {
  // ipc.js は electron を require するので、その部分だけ差し替えて読む
  const Module = require('module');
  const orig = Module._load;
  Module._load = function (req, ...rest) { return req === 'electron' ? { ipcMain: {}, dialog: {}, shell: {}, app: { on() {} } } : orig.call(this, req, ...rest); };
  let ipc;
  try { ipc = require('../src/main/ipc'); } finally { Module._load = orig; }
  assert.strictEqual(ipc.cleanAnswer('\x1b[38;5;141m> \x1b[0mみかん\n\x1b[?25h'), 'みかん');
  assert.strictEqual(ipc.stripAnsi('plain'), 'plain');
  assert.strictEqual(ipc.cleanAnswer('> 引用ではなく入力欄\n本文'), '引用ではなく入力欄\n本文');
});

test('kiro の一覧から、このターン以後に更新された最新を選ぶ', () => {
  const json = JSON.stringify([{ cwd: '/r', sessions: [
    { sessionId: 'old', updatedAt: '2026-01-01T00:00:00Z' },
    { sessionId: 'new', updatedAt: '2026-09-05T00:00:10Z' },
    { sessionId: 'newer', updatedAt: '2026-09-05T00:00:20Z' },
  ] }, { cwd: '/other', sessions: [{ sessionId: 'x', updatedAt: '2026-09-06T00:00:00Z' }] }]);
  assert.strictEqual(agentCli.pickListedSession(json, '/r', Date.parse('2026-09-05T00:00:00Z')), 'newer');
  assert.strictEqual(agentCli.pickListedSession(json, '/r', Date.parse('2026-09-07T00:00:00Z')), '');
  assert.strictEqual(agentCli.pickListedSession('not json', '/r', 0), '');
});

test('定義の一覧は同名先勝ちで、command[0] の有無を印にする', () => {
  const all = agentCli.list('');
  const names = all.map((a) => a.name);
  assert.ok(names.includes('claude') && names.includes('copilot') && names.includes('kiro'));
  assert.strictEqual(new Set(names).size, names.length);
  assert.strictEqual(all.find((a) => a.name === 'kiro').session, 'list');
});

test('店: 会話の作成・追記・一覧・更新・削除', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-'));
  store.addRepo(ud, '/repo/a');
  assert.ok(store.isRegistered(ud, '/repo/a') && !store.isRegistered(ud, '/repo/b'));
  const s = store.createSession(ud, { repo: '/repo/a', cli: 'claude', readonly: true });
  store.appendMessage(ud, s.id, { role: 'user', text: '最初の依頼\n2 行目' });
  store.appendMessage(ud, s.id, { role: 'assistant', text: '答え', code: 0 });
  store.updateSession(ud, s.id, { cliSession: 'X', ignored: 'no' });
  const got = store.readSession(ud, s.id);
  assert.strictEqual(got.title, '最初の依頼');
  assert.strictEqual(got.cliSession, 'X');
  assert.strictEqual(got.ignored, undefined);
  assert.strictEqual(got.messages.length, 2);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/a').map((x) => x.id), [s.id]);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/b'), []);
  assert.throws(() => store.readSession(ud, '../etc'), /不正/);
  store.removeSession(ud, s.id);
  assert.deepStrictEqual(store.listSessions(ud, ''), []);
});

test('git: 作業ツリーの変更を読む', async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-git-'));
  const run = (args) => execFileSync('git', ['-C', repo, ...args], { stdio: 'pipe' });
  run(['init', '-q']);
  run(['config', 'user.email', 't@example.com']);
  run(['config', 'user.name', 't']);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
  run(['add', '.']);
  run(['commit', '-qm', 'init']);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'two\n');
  fs.writeFileSync(path.join(repo, 'b.txt'), 'new\n');
  const res = await git.changes(repo);
  assert.deepStrictEqual(res.files.map((f) => [f.file, f.label]), [['a.txt', '変更'], ['b.txt', '新規']]);
  assert.ok(res.diff.includes('-one') && res.diff.includes('+two'));
  assert.ok((await git.fileDiff(repo, 'b.txt')).includes('+new'));
  const not = await git.changes(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-nogit-')));
  assert.ok(not.error);
});
