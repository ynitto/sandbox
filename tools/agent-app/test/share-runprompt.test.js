'use strict';

// 共有の参加者として CLI を 1 回起こす口（ipc.runPrompt）を、偽の CLI で確かめる。
// ipc.js は electron を require するので、その部分だけ差し替えて読む（app.test.js と同じ）。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'share-runprompt-'));
const agentsDir = path.join(userData, 'agents');
fs.mkdirSync(agentsDir, { recursive: true });
const cliScript = path.join(userData, 'fake-cli.js');
fs.writeFileSync(cliScript, `
let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  if (process.argv.includes('--quota')) { process.stderr.write('usage limit reached\\n'); process.exit(1); }
  if (process.argv.includes('--hang')) { setTimeout(() => {}, 60000); return; }
  process.stdout.write('ECHO:' + input.trim() + ' readonly=' + process.argv.includes('--ro') + ' nosess=' + process.argv.includes('--no-sess') + '\\n');
});
`, 'utf8');
const def = (name, extra) => fs.writeFileSync(path.join(agentsDir, `${name}.json`), JSON.stringify({
  name, command: ['node', cliScript, ...(extra || [])], prompt_via: 'stdin', output: 'stdout',
  readonly_args: ['--ro'], write_args: ['--rw'], no_session_args: ['--no-sess'],
  errors: [{ match: 'usage limit reached', class: 'quota', quota_kind: 'exhausted', hint: '枠が枯渇' }],
}, null, 2));
def('fake');
def('fakequota', ['--quota']);
def('fakehang', ['--hang']);
fs.writeFileSync(path.join(agentsDir, 'kiro.json'), JSON.stringify({ name: 'kiro', command: ['node', cliScript] }));
process.env.KIRO_AGENTS_DIR = agentsDir;

function loadIpc() {
  const orig = Module._load;
  Module._load = function (req, ...rest) {
    return req === 'electron' ? { ipcMain: { handle() {} }, dialog: {}, shell: {}, app: { on() {}, getPath: () => userData } } : orig.call(this, req, ...rest);
  };
  try { return require('../src/main/ipc'); } finally { Module._load = orig; }
}

test('runPrompt: 読み取り専用・セッション無しで起こし、本文を stdin で渡して答えを返す', async () => {
  const ipc = loadIpc();
  const run = ipc.runPrompt({ cli: 'fake', prompt: 'こんにちは', cwd: userData, timeoutMs: 10000 });
  const out = await run.done;
  assert.equal(out.code, 0);
  assert.equal(out.text, 'ECHO:こんにちは readonly=true nosess=true');
  assert.equal(out.errorClass, '');
  assert.ok(out.elapsedMs >= 0);
});

test('runPrompt: 定義の errors で枠切れを分類し、時間切れと停止は transient にする', async () => {
  const ipc = loadIpc();
  const quota = await ipc.runPrompt({ cli: 'fakequota', prompt: 'x', cwd: userData, timeoutMs: 10000 }).done;
  assert.equal(quota.code, 1);
  assert.equal(quota.errorClass, 'quota');
  assert.equal(quota.quotaKind, 'exhausted');
  assert.equal(quota.error, '枠が枯渇');
  const timeout = await ipc.runPrompt({ cli: 'fakehang', prompt: 'x', cwd: userData, timeoutMs: 300 }).done;
  assert.equal(timeout.stopped, true);
  assert.equal(timeout.errorClass, 'transient');
  assert.equal(timeout.error, '時間切れ');
  const stopped = ipc.runPrompt({ cli: 'fakehang', prompt: 'x', cwd: userData, timeoutMs: 10000 });
  setTimeout(() => stopped.stop('依頼者が取り下げた'), 100);
  const out = await stopped.done;
  assert.equal(out.stopped, true);
  assert.equal(out.error, '依頼者が取り下げた');
});

test('normalizeRepoUrl: ssh と https、.git の有無、大小文字を同じ鍵にする', () => {
  const ipc = loadIpc();
  const keys = new Set(['git@forge:Team/App.git', 'https://forge/team/app', 'ssh://git@forge/team/app.git/'].map(ipc.normalizeRepoUrl));
  assert.equal(keys.size, 1, [...keys].join(' | '));
});
