'use strict';

// Electron を起動せずに固定する: 構文・argv の組み立て・セッションの保存・git の読み取り・ファイル閲覧。

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
const files = require('../src/main/files');
const text = require('../src/main/text');

const SRC = path.join(__dirname, '..', 'src');

test('main / ipc / preload / renderer は構文検査を通る', () => {
  for (const f of ['main/main.js', 'main/ipc.js', 'main/agentCli.js', 'main/store.js', 'main/git.js', 'main/host.js', 'main/tmux.js', 'main/files.js', 'main/text.js',
    'preload.js', 'renderer/renderer.js', 'renderer/md.js', 'renderer/term.js', 'renderer/files.js']) {
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

test('index.html が読むスクリプトは vendor.js が写すものと画面のもので揃う', () => {
  const html = fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8');
  const vendor = require('../scripts/vendor');
  const names = new Set(vendor.FILES.map(([, name]) => name));
  for (const m of html.matchAll(/(?:src|href)="vendor\/([^"]+)"/g)) {
    const f = m[1];
    if (f.startsWith('hljs/')) assert.ok(vendor.HLJS_EXTRA.includes(f.slice(5).replace('.min.js', '')), f);
    else assert.ok(names.has(f), `vendor.js が写さない: ${f}`);
  }
  for (const m of html.matchAll(/src="([^"/]+\.js)"/g)) assert.ok(fs.existsSync(path.join(SRC, 'renderer', m[1])), m[1]);
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

// 対話起動（tmux）。write_args は interactive 節のものだけで、ヘッドレスの危険フラグは継承しない。
test('argv: 対話起動は interactive 節から組み、プロンプトを含まない', () => {
  const claude = agentCli.load('claude');
  assert.ok(claude.interactive && claude.interactive.busyPattern.includes('esc to interrupt'));
  const first = agentCli.interactiveCmd(claude, { model: 'M' });
  assert.deepStrictEqual(first.argv, ['claude', '--session-id', first.mintedSession, '--model', 'M']);
  assert.ok(!first.argv.includes('--dangerously-skip-permissions'));
  const ro = agentCli.interactiveCmd(claude, { readonly: true, cliSession: 'S' });
  assert.deepStrictEqual(ro.argv, ['claude', '--resume', 'S', '--permission-mode', 'plan']);
  assert.strictEqual(ro.mintedSession, '');

  const kiro = agentCli.interactiveCmd(agentCli.load('kiro'), { model: 'M' });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--trust-all-tools', '--model', 'M']);
  const kiroAgain = agentCli.interactiveCmd(agentCli.load('kiro'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(kiroAgain.argv, ['kiro-cli', 'chat', '--trust-all-tools']);
  assert.ok(kiroAgain.warning, '再開手段が無い CLI は起動し直すと文脈が切れる旨を出す');

  const codex = agentCli.interactiveCmd(agentCli.load('codex'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(codex.argv, ['codex', 'resume', '--last']);
  // interactive 節の無い定義は対話起動できない（一覧の印も false）
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-agents-'));
  fs.writeFileSync(path.join(dir, 'plain.json'), JSON.stringify({ command: ['plain-cli', '-p'], prompt_via: 'stdin' }));
  const saved = process.env.KIRO_AGENTS_DIR;
  process.env.KIRO_AGENTS_DIR = dir;
  try {
    assert.strictEqual(agentCli.load('plain').interactive, null);
    assert.throws(() => agentCli.interactiveCmd(agentCli.load('plain'), {}), /interactive/);
    assert.strictEqual(agentCli.list('').find((a) => a.name === 'plain').interactive, false);
  } finally { process.env.KIRO_AGENTS_DIR = saved; }
  assert.ok(agentCli.list('').find((a) => a.name === 'claude').interactive);
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
  assert.strictEqual(text.stripAnsi('\x1b]0;title\x07x\x1b]8;;http://a\x1b\\y'), 'xy', 'OSC は BEL でも ST でも閉じる');
  // Windows のヘッドレスは wsl.exe に載せ、cwd を WSL 表記へ直す
  const spec = ipc.spawnSpec('claude', ['-p'], { cwd: 'C:\\work\\repo', env: { A: '1' }, distro: 'Ubuntu' });
  if (process.platform === 'win32') {
    assert.strictEqual(spec.command, 'wsl.exe');
    assert.ok(spec.args.join(' ').includes("cd '/mnt/c/work/repo'"));
  } else {
    assert.strictEqual(spec.command, 'claude');
  }
});

test('ERE → RegExp: POSIX のブラケットクラスを写す', () => {
  const re = text.ereToRegExp('^[[:space:]]*[>?❯›][[:space:]]*$|│[[:space:]]*[>❯›]');
  assert.ok(re.test('  > '));
  assert.ok(re.test('│ ❯ 依頼を書く'));
  assert.ok(!re.test('> 本文がある'));
  assert.ok(text.ereToRegExp('^[[:blank:]]*[>?❯›]([[:blank:]].*)?$').test('> foo'));
  assert.strictEqual(text.ereToRegExp('('), null);
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
  assert.strictEqual(s.transport, 'tmux', '既定は tmux');
  store.appendMessage(ud, s.id, { role: 'user', text: '最初の依頼\n2 行目' });
  store.appendMessage(ud, s.id, { role: 'assistant', text: '答え', code: 0 });
  store.updateSession(ud, s.id, { cliSession: 'X', ignored: 'no', transport: 'headless' });
  const got = store.readSession(ud, s.id);
  assert.strictEqual(got.title, '最初の依頼');
  assert.strictEqual(got.cliSession, 'X');
  assert.strictEqual(got.transport, 'headless');
  assert.strictEqual(got.ignored, undefined);
  assert.strictEqual(got.messages.length, 2);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/a').map((x) => x.id), [s.id]);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/b'), []);
  assert.throws(() => store.readSession(ud, '../etc'), /不正/);
  store.removeSession(ud, s.id);
  assert.deepStrictEqual(store.listSessions(ud, ''), []);
  const cfg = store.saveConfig(ud, { wslDistro: ' Ubuntu ', transport: 'bogus', view: 'files', lastFiles: { '/repo/a': 'README.md' } });
  assert.strictEqual(cfg.wslDistro, 'Ubuntu');
  assert.strictEqual(cfg.transport, 'tmux');
  assert.strictEqual(cfg.view, 'files');
  assert.strictEqual(cfg.lastFiles['/repo/a'], 'README.md');
});

function makeRepo() {
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
  return repo;
}

test('git: 作業ツリーの変更をホストのシェル経由で読む', async () => {
  const repo = makeRepo();
  const res = await git.changes(repo);
  assert.deepStrictEqual(res.files.map((f) => [f.file, f.label]), [['a.txt', '変更'], ['b.txt', '新規']]);
  assert.ok(res.diff.includes('-one') && res.diff.includes('+two'));
  assert.ok(res.branch, 'ブランチ名が付く');
  assert.ok((await git.fileDiff(repo, 'b.txt')).includes('+new'));
  const not = await git.changes(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-nogit-')));
  assert.ok(not.error);
  require('../src/main/host').closeAll();
});

test('ファイル: ツリー・本文・言語判定・外へ出ない', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-files-'));
  fs.mkdirSync(path.join(repo, 'src', 'deep'), { recursive: true });
  fs.mkdirSync(path.join(repo, '.git'));
  fs.writeFileSync(path.join(repo, 'README.md'), '# Title\n\n```mermaid\ngraph TD; A-->B\n```\n');
  fs.writeFileSync(path.join(repo, 'src', 'index.ts'), 'export const x = 1;\n');
  fs.writeFileSync(path.join(repo, 'src', 'deep', 'Dockerfile'), 'FROM node\n');
  fs.writeFileSync(path.join(repo, 'bin.dat'), Buffer.from([0, 1, 2, 3]));
  fs.writeFileSync(path.join(repo, 'pic.png'), Buffer.from('89504e470d0a1a0a', 'hex'));
  const root = files.listDir(repo, '');
  assert.deepStrictEqual(root.entries.map((e) => e.name), ['src', 'bin.dat', 'pic.png', 'README.md'], 'ディレクトリ先・名前順・.git は出さない');
  assert.strictEqual(root.entries.find((e) => e.name === 'README.md').language, 'markdown');
  const src = files.listDir(repo, 'src');
  assert.deepStrictEqual(src.entries.map((e) => e.rel), ['src/deep', 'src/index.ts']);
  const ts = files.readFile(repo, 'src/index.ts');
  assert.strictEqual(ts.kind, 'text');
  assert.strictEqual(ts.language, 'typescript');
  assert.strictEqual(ts.lines, 2);
  assert.strictEqual(files.readFile(repo, 'bin.dat').kind, 'binary');
  assert.ok(files.readFile(repo, 'pic.png').dataUrl.startsWith('data:image/png;base64,'));
  assert.strictEqual(files.languageOf('src/deep/Dockerfile'), 'dockerfile');
  assert.strictEqual(files.languageOf('Makefile'), 'makefile');
  assert.strictEqual(files.languageOf('x.unknownext'), '');
  assert.strictEqual(files.languageOf('.gitignore'), 'plaintext');
  assert.throws(() => files.readFile(repo, '../../etc/passwd'), /外/);
  assert.throws(() => files.listDir(repo, '..'), /外/);
  const hits = files.find(repo, 'index');
  assert.deepStrictEqual(hits.map((h) => h.rel), ['src/index.ts']);
});
