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
const attachments = require('../src/main/attachments');
const automationIpc = require('../src/main/automation/ipc');

const SRC = path.join(__dirname, '..', 'src');

test('main / ipc / preload / renderer は構文検査を通る', () => {
  for (const f of ['main/main.js', 'main/ipc.js', 'main/automation/ipc.js', 'main/agentCli.js', 'main/store.js', 'main/git.js', 'main/host.js', 'main/tmux.js', 'main/files.js', 'main/text.js', 'main/attachments.js',
    'preload.js', 'renderer/renderer.js', 'renderer/md.js', 'renderer/term.js', 'renderer/files.js', 'renderer/vendor/statemachine/renderer.js']) {
    execFileSync(process.execPath, ['--check', path.join(SRC, f)]);
  }
  const main = fs.readFileSync(path.join(SRC, 'main/main.js'), 'utf8');
  assert.ok(main.includes('contextIsolation: true') && main.includes('sandbox: true'));
  assert.ok(fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8').includes("script-src 'self'"));
  // 画面の出し分けは hidden 属性でやる。label や .seg のように display を明示した要素では
  // 作者スタイルが UA の `[hidden] { display: none }` に勝ってしまうので、全体規則で押さえる。
  assert.match(fs.readFileSync(path.join(SRC, 'renderer/styles.css'), 'utf8'), /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
});

test('preload の窓口と ipc のチャネルが 1 対 1', () => {
  const pre = fs.readFileSync(path.join(SRC, 'preload.js'), 'utf8');
  const ipc = fs.readFileSync(path.join(SRC, 'main/ipc.js'), 'utf8');
  const makerIpc = fs.readFileSync(path.join(__dirname, '..', '..', 'statemachine-maker', 'src', 'main', 'ipc.js'), 'utf8');
  const invoked = [...pre.matchAll(/invoke\('([\w:]+)'/g)].map((m) => m[1]);
  const handled = [
    ...[...ipc.matchAll(/handle\('([\w:]+)'/g)].map((m) => m[1]),
    ...[...makerIpc.matchAll(/register\('([\w:]+)'/g)].map((m) => `automation:${m[1]}`),
  ];
  assert.deepStrictEqual([...new Set(invoked)].sort(), handled.sort());
});

test('index.html が読むスクリプトは vendor.js が写すものと画面のもので揃う', () => {
  const html = [
    fs.readFileSync(path.join(SRC, 'renderer/index.html'), 'utf8'),
    fs.readFileSync(path.join(SRC, 'renderer/automation-frame.html'), 'utf8'),
  ].join('\n');
  const vendor = require('../scripts/vendor');
  const names = new Set(vendor.FILES.map(([, name]) => name));
  for (const m of html.matchAll(/(?:src|href)="vendor\/([^"]+)"/g)) {
    const f = m[1];
    if (f.startsWith('hljs/')) assert.ok(vendor.HLJS_EXTRA.includes(f.slice(5).replace('.min.js', '')), f);
    else if (f === 'statemachine/renderer.js') assert.ok(fs.existsSync(path.join(SRC, 'renderer', 'vendor', f)), f);
    else assert.ok(names.has(f), `vendor.js が写さない: ${f}`);
  }
  for (const m of html.matchAll(/src="([^"/]+\.js)"/g)) assert.ok(fs.existsSync(path.join(SRC, 'renderer', m[1])), m[1]);
});

test('自動化は agent-app の登録リポジトリと設定を共有する', () => {
  const cfg = automationIpc.automationConfig({
    repos: ['/repo/a', '/repo/b'], lastRepo: '/repo/b',
    automationSkillDir: '/skill', automationAgent: 'codex', automationModel: 'm',
  });
  assert.deepStrictEqual(cfg, {
    roots: ['/repo/a', '/repo/b'], lastRoot: '/repo/b', skillDir: '/skill', agent: 'codex', model: 'm',
  });
  assert.deepStrictEqual(automationIpc.automationPatch({
    roots: ['/ignored'], lastRoot: '/repo/a', skillDir: '/next', agent: 'aider', model: '',
  }), {
    lastRepo: '/repo/a', automationSkillDir: '/next', automationAgent: 'aider', automationModel: '',
  });
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

// ターンごとにエージェントを変えられる: history は「その CLI がまだ見ていない分」で、
// セッションを再開できる CLI でも、別の CLI で進めた分があればそれを依頼の前に添える。
test('argv: 別の CLI で進めた分は、戻ってきた CLI へ追いつかせる', () => {
  const unseen = [
    { role: 'user', text: 'codex で直して', cli: 'codex', attachments: [{ rel: 'src/a.ts', name: 'a.ts' }] },
    { role: 'assistant', text: '直した', cli: 'codex' },
  ];
  // 再開できる CLI（claude）に未読があれば、resume しつつ本文の頭に添える
  const back = agentCli.turnCmd(agentCli.load('claude'), { prompt: '確認して', cliSession: 'S', history: unseen });
  assert.deepStrictEqual(back.argv.slice(0, 3), ['claude', '--resume', 'S']);
  assert.ok(back.stdin.startsWith('この会話には、あなたのセッションの外で進んだやり取りがある'), back.stdin);
  assert.ok(back.stdin.includes('[user → codex] codex で直して\n（添付: src/a.ts）') && back.stdin.includes('[assistant (codex)] 直した'));
  assert.ok(back.stdin.endsWith('新しい依頼:\n確認して'));
  // この会話で初めて使う CLI は、新しいセッションを発行して全部を添える
  const first = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', history: unseen });
  assert.ok(first.mintedSession && first.stdin.startsWith('これまでの会話（同じセッションの続きとして扱うこと）'));
  const codex = agentCli.turnCmd(agentCli.load('codex'), { prompt: 'p', history: unseen });
  assert.ok(codex.stdin.startsWith('これまでの会話'), 'capture 型の CLI も初回は再送する');
  // 未読が無ければ本文はそのまま
  assert.strictEqual(agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', cliSession: 'S', history: [] }).stdin, 'p');
  // 添付は file_flag を宣言する CLI にだけ argv で渡る（本文には呼び出し側が書く）
  const aider = agentCli.turnCmd(agentCli.load('aider'), { prompt: 'p', files: ['/tmp/x.png', '/tmp/y.md'] });
  assert.ok(aider.argv.includes('--file') && aider.argv[aider.argv.indexOf('--file') + 1] === '/tmp/x.png' && aider.argv.filter((a) => a === '--file').length === 2);
  const claude = agentCli.turnCmd(agentCli.load('claude'), { prompt: 'p', files: ['/tmp/x.png'] });
  assert.ok(!claude.argv.includes('/tmp/x.png'));
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
  assert.strictEqual(ro.resumed, true, 'resume できたなら文脈は引き継がれている');
  assert.strictEqual(first.resumed, false);

  const kiro = agentCli.interactiveCmd(agentCli.load('kiro'), { model: 'M' });
  assert.deepStrictEqual(kiro.argv, ['kiro-cli', 'chat', '--trust-all-tools', '--model', 'M']);
  const kiroAgain = agentCli.interactiveCmd(agentCli.load('kiro'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(kiroAgain.argv, ['kiro-cli', 'chat', '--trust-all-tools']);
  assert.ok(kiroAgain.warning, '再開手段が無い CLI は起動し直すと履歴を添える旨を出す');
  assert.strictEqual(kiroAgain.resumed, false, '文脈は引き継げていない（最初の依頼で追いつかせる）');

  const codex = agentCli.interactiveCmd(agentCli.load('codex'), { history: [{ role: 'user', text: 'x' }] });
  assert.deepStrictEqual(codex.argv, ['codex', 'resume', '--last']);
  assert.strictEqual(codex.resumed, true);
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
  // ターンの起動条件は画面から届いたものが勝ち、無ければ会話の既定
  const sess = { cli: 'claude', model: 'm1', readonly: false };
  assert.deepStrictEqual(ipc.turnSpec(sess, { prompt: ' p ' }), { cli: 'claude', model: 'm1', readonly: false, text: 'p' });
  assert.deepStrictEqual(ipc.turnSpec(sess, { prompt: 'p', cli: 'Codex', model: '', readonly: true }), { cli: 'codex', model: '', readonly: true, text: 'p' });
  assert.throws(() => ipc.turnSpec(sess, { prompt: ' ' }), /空/);
  assert.strictEqual(ipc.turnSpec(sess, { prompt: '', attachments: [{ rel: 'a' }] }).text, '', '添付だけの依頼は通す');
  assert.ok(ipc.sameLaunch({ cli: 'a', model: '', readonly: false }, { cli: 'a', readonly: 0 }));
  assert.ok(!ipc.sameLaunch({ cli: 'a', model: 'x' }, { cli: 'a', model: 'y' }));
  assert.ok(!ipc.sameLaunch(null, { cli: 'a' }));
  // 添付: 写したファイルはホスト側のパスで、作業フォルダの中のファイルは相対パスで本文に添える
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-'));
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-repo-'));
  fs.writeFileSync(path.join(repo, 'a.ts'), 'x');
  const staged = attachments.stage(ud, 'shot.png', Buffer.from([1, 2, 3]));
  const w = ipc.withAttachments(ud, '依頼', [{ id: staged.id, name: 'shot.png' }, { rel: './a.ts' }, { rel: 'a.ts' }], { fsDir: repo });
  assert.deepStrictEqual(w.atts, [{ id: staged.id, name: 'shot.png', size: 3 }, { rel: 'a.ts', name: 'a.ts' }, { rel: 'a.ts', name: 'a.ts' }]);
  assert.deepStrictEqual(w.files, [require('../src/main/host').toHostPath(attachments.pathOf(ud, staged.id, 'shot.png'))]);
  assert.ok(w.prompt.startsWith('依頼\n\n添付ファイル') && w.prompt.includes('- a.ts（作業フォルダの中）') && w.prompt.includes(`/${staged.id}/shot.png`), w.prompt);
  assert.strictEqual(ipc.withAttachments(ud, '依頼', [], { fsDir: repo }).prompt, '依頼');
  assert.ok(ipc.withAttachments(ud, '', [{ rel: 'a.ts' }], { fsDir: repo }).prompt.startsWith('添付ファイル'));
  assert.throws(() => ipc.withAttachments(ud, 'p', [{ rel: '../etc/passwd' }], { fsDir: repo }), /外/);
  assert.throws(() => ipc.withAttachments(ud, 'p', [{ id: staged.id, name: 'other.png' }], { fsDir: repo }), /見つかりません/);
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
  store.updateSession(ud, s.id, { cli: 'codex', model: 'm', ignored: 'no', transport: 'headless', worktree: 'other', live: { cli: 'codex', model: 'm', readonly: false } });
  store.setCliEntry(ud, s.id, 'claude', { id: 'X' });
  store.setCliEntry(ud, s.id, 'claude', { seen: 2 });
  const got = store.readSession(ud, s.id);
  assert.strictEqual(got.worktree, '', '作業フォルダは会話を作ったあとは変えられない');
  assert.strictEqual(got.title, '最初の依頼');
  assert.deepStrictEqual([got.cli, got.model], ['codex', 'm'], 'エージェントとモデルは「次のターン」の既定として変えられる');
  assert.deepStrictEqual(store.cliEntry(got, 'claude'), { id: 'X', seen: 2 }, 'CLI ごとにセッション ID と見た数を持つ');
  assert.strictEqual(store.cliEntry(got, 'codex'), null);
  assert.deepStrictEqual(got.live, { cli: 'codex', model: 'm', readonly: false });
  assert.strictEqual(got.transport, 'headless');
  assert.strictEqual(got.ignored, undefined);
  assert.strictEqual(got.messages.length, 2);
  assert.strictEqual(store.listSessions(ud, '/repo/a')[0].cli, 'codex');
  // 以前の形（cliSession 1 つ）は読むときに cliSessions へ写す
  const legacyId = '00000000-0000-4000-8000-000000000001';
  fs.writeFileSync(path.join(store.sessionsDir(ud), `${legacyId}.json`), JSON.stringify({ id: legacyId, repo: '/repo/a', cli: 'claude', cliSession: 'OLD', messages: [{ role: 'user', text: 'a' }, { role: 'assistant', text: 'b' }], updatedAt: '2026-01-01T00:00:00Z' }));
  const legacy = store.readSession(ud, legacyId);
  assert.deepStrictEqual(store.cliEntry(legacy, 'claude'), { id: 'OLD', seen: 2 });
  assert.strictEqual(legacy.cliSession, undefined);
  assert.deepStrictEqual(store.readAllSessions(ud).map((x) => x.id).sort(), [s.id, legacyId].sort());
  store.removeSession(ud, legacyId);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/a').map((x) => x.id), [s.id]);
  assert.deepStrictEqual(store.listSessions(ud, '/repo/b'), []);
  // 作業フォルダ（worktree）を持つ会話は、名前とブランチを覚えて一覧にも出す
  const w = store.createSession(ud, { repo: '/repo/a', cli: 'claude', worktree: 'feature-x', branch: 'feature/x' });
  assert.deepStrictEqual([w.worktree, w.branch], ['feature-x', 'feature/x']);
  const listed = store.listSessions(ud, '/repo/a').find((x) => x.id === w.id);
  assert.deepStrictEqual([listed.worktree, listed.branch], ['feature-x', 'feature/x']);
  store.removeSession(ud, w.id);
  assert.throws(() => store.readSession(ud, '../etc'), /不正/);
  store.removeSession(ud, s.id);
  assert.deepStrictEqual(store.listSessions(ud, ''), []);
  const cfg = store.saveConfig(ud, { wslDistro: ' Ubuntu ', transport: 'bogus', view: 'files', lastFiles: { '/repo/a': 'README.md' } });
  assert.strictEqual(cfg.wslDistro, 'Ubuntu');
  assert.strictEqual(cfg.transport, 'tmux');
  assert.strictEqual(cfg.useWorktree, true, '作業フォルダの機能は既定で使える');
  assert.strictEqual(store.saveConfig(ud, { useWorktree: false }).useWorktree, false, '切ったら覚える');
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
  // 作業フォルダの置き場（.worktrees）はリポジトリの写しなので、本体のツリーには出さない
  fs.mkdirSync(path.join(repo, '.worktrees', 'feature-x', 'src'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.worktrees', 'feature-x', 'src', 'index.ts'), 'export const x = 1;\n');
  const root = files.listDir(repo, '');
  assert.deepStrictEqual(root.entries.map((e) => e.name), ['src', 'bin.dat', 'pic.png', 'README.md'], 'ディレクトリ先・名前順・.git と .worktrees は出さない');
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
  assert.deepStrictEqual(hits.map((h) => h.rel), ['src/index.ts'], '名前検索が worktree の分だけ重複しない');
  // 作業フォルダ自身を根にすれば、その中は普通に見える
  const inWt = files.listDir(path.join(repo, '.worktrees', 'feature-x'), '');
  assert.deepStrictEqual(inWt.entries.map((e) => e.name), ['src']);
});

test('添付: 写す・引く・消す。名前は 1 要素に丸め、外へ出ない', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-att-'));
  const a = attachments.stage(ud, '../../evil/../shot.png', Buffer.from('png'));
  assert.strictEqual(a.name, 'shot.png');
  assert.match(a.id, /^[0-9a-f-]{36}$/);
  const r = attachments.resolve(ud, a.id, a.name);
  assert.strictEqual(fs.readFileSync(r.path, 'utf8'), 'png');
  assert.ok(r.path.startsWith(path.join(ud, 'attachments', a.id)));
  assert.throws(() => attachments.resolve(ud, '../x', 'shot.png'), /不正/);
  assert.throws(() => attachments.resolve(ud, a.id, 'nope.png'), /見つかりません/);
  assert.throws(() => attachments.stage(ud, 'big.bin', Buffer.alloc(attachments.MAX_BYTES + 1)), /大きすぎます/);
  const src = path.join(ud, 'src.md');
  fs.writeFileSync(src, '# doc');
  const b = attachments.stageFile(ud, src);
  assert.strictEqual(b.name, 'src.md');
  assert.strictEqual(b.size, 5);
  assert.throws(() => attachments.stageFile(ud, ud), /ファイルではありません/);
  // 会話を消すと、そのメッセージが参照している添付も消える
  attachments.discardAll(ud, { messages: [{ role: 'user', attachments: [{ id: a.id, name: a.name }, { rel: 'x' }] }] });
  assert.ok(!fs.existsSync(path.join(ud, 'attachments', a.id)));
  assert.ok(fs.existsSync(path.join(ud, 'attachments', b.id)));
  attachments.discard(ud, b.id);
  assert.ok(!fs.existsSync(path.join(ud, 'attachments', b.id)));
  assert.strictEqual(attachments.discard(ud, b.id), true, '無くても失敗にしない');
  // 起動時の掃除: どの会話も参照していないものだけ消す
  const kept = attachments.stage(ud, 'kept.txt', Buffer.from('k'));
  const orphan = attachments.stage(ud, 'orphan.txt', Buffer.from('o'));
  fs.mkdirSync(path.join(ud, 'attachments', 'not-an-id'));
  assert.strictEqual(attachments.sweep(ud, [{ messages: [{ role: 'user', attachments: [{ id: kept.id, name: 'kept.txt' }] }] }]), 1);
  assert.ok(fs.existsSync(path.join(ud, 'attachments', kept.id)) && !fs.existsSync(path.join(ud, 'attachments', orphan.id)) && fs.existsSync(path.join(ud, 'attachments', 'not-an-id')));
  assert.strictEqual(attachments.sweep(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-none-')), []), 0);
});
