'use strict';

const { ipcMain, dialog, shell, app } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const agentCli = require('./agentCli');
const store = require('./store');
const git = require('./git');

function userData() { return app.getPath('userData'); }

// すべてのハンドラを {ok, data|error} に揃える。
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, args) => {
    try {
      return { ok: true, data: await fn(args || {}, event) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

// 触ってよいのは**登録したリポジトリだけ**。登録に無いパスは、実在していても断る。
function requireRepo(repo) {
  const dir = String(repo || '').trim();
  if (!dir) throw new Error('リポジトリを選んでください');
  if (!store.isRegistered(userData(), dir)) throw new Error('登録していないフォルダです');
  let st;
  try { st = fs.statSync(dir); } catch { st = null; }
  if (!st || !st.isDirectory()) throw new Error('フォルダが見つかりません');
  return dir;
}

// Windows では npm の global 導入が `copilot.cmd` を置く。Node の spawn は `.cmd` を直接起動
// できないので cmd /c に載せる（shell: true は引用が二重に解釈されて壊れるので使わない）。
function spawnSpec(command, args) {
  if (process.platform !== 'win32') return { command, args, extra: {} };
  const file = agentCli.resolvePath(command) || command;
  if (!/\.(cmd|bat)$/i.test(file)) return { command: file, args, extra: {} };
  const quote = (s) => `"${String(s).replace(/"/g, '""')}"`;
  return {
    command: process.env.COMSPEC || 'cmd.exe',
    args: ['/d', '/s', '/c', `"${[quote(file), ...args.map(quote)].join(' ')}"`],
    extra: { windowsVerbatimArguments: true },
  };
}

function capture(argv, cwd) {
  return new Promise((resolve) => {
    execFile(argv[0], argv.slice(1), { cwd, windowsHide: true, timeout: 30000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => resolve(err && !stdout ? '' : String(stdout || '')));
  });
}

// 走っているターン。セッション ID → 子プロセス。セッションが違えば同時に走ってよい。
const running = new Map();

// 端末向けの装飾を剥がす。kiro は --no-interactive でも色と入力欄の `> ` を出す。
function stripAnsi(text) {
  return String(text).replace(/\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]/g, '');
}

function cleanAnswer(text) {
  return stripAnsi(text).trim().replace(/^>\s+/, '');
}

function lineEmitter(onLine) {
  let buf = '';
  return (chunk) => {
    buf += chunk.toString('utf8');
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      onLine(stripAnsi(buf.slice(0, i)).replace(/\r$/, ''));
      buf = buf.slice(i + 1);
    }
  };
}

function killTree(child) {
  try {
    if (process.platform === 'win32') execFile('taskkill', ['/pid', String(child.pid), '/T', '/F'], () => {});
    else process.kill(-child.pid, 'SIGTERM');
  } catch {
    try { child.kill(); } catch { /* 既に終わっている */ }
  }
}

function runTurn(id, prompt, send) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  if (running.has(id)) throw new Error('このセッションは応答中です');
  const text = String(prompt || '').trim();
  if (!text) throw new Error('依頼が空です');
  const spec = agentCli.load(sess.cli, repo);
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const turn = agentCli.turnCmd(spec, {
    prompt: text, model: sess.model, readonly: sess.readonly, cliSession: sess.cliSession, history,
  });
  store.appendMessage(ud, id, { role: 'user', text });
  if (turn.mintedSession) store.updateSession(ud, id, { cliSession: turn.mintedSession });

  const startedAt = Date.now();
  const spec2 = spawnSpec(turn.command, turn.args);
  let child;
  try {
    child = spawn(spec2.command, spec2.args, {
      cwd: repo, env: { ...process.env, ...turn.env }, windowsHide: true,
      detached: process.platform !== 'win32',          // 停止で孫プロセスまで止める（グループごと）
      ...spec2.extra,
    });
  } catch (err) {
    throw new Error(`起動できません: ${(err && err.message) || err}`);
  }
  running.set(id, child);
  send('turn:started', { id, argv: turn.argv, warning: turn.readonlyWarning });

  let stdout = '';
  let stderr = '';
  let stopped = false;
  child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
  child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
  child.stdout.on('data', lineEmitter((line) => send('turn:line', { id, kind: 'stdout', text: line })));
  child.stderr.on('data', lineEmitter((line) => send('turn:line', { id, kind: 'stderr', text: line })));
  child.on('error', (err) => { stderr += `\n起動エラー: ${(err && err.message) || err}`; });
  child.stdin.on('error', () => { /* 先に終わった CLI へ書いた EPIPE */ });
  child.stdin.end(turn.stdin == null ? '' : turn.stdin);
  child.stop = () => { stopped = true; killTree(child); };

  child.on('close', async (code) => {
    running.delete(id);
    let answer = '';
    if (turn.outputFile) {
      try { answer = fs.readFileSync(turn.outputFile, 'utf8'); } catch { /* 書かれなかった */ }
      try { fs.unlinkSync(turn.outputFile); } catch { /* 無ければよい */ }
    } else {
      answer = stdout;
    }
    answer = cleanAnswer(answer);
    stderr = stripAnsi(stderr);
    const failed = stopped || code !== 0 || !answer;
    const rule = failed ? agentCli.classifyError(spec, `${stderr}\n${stdout}`) : null;
    try {
      if (!stopped && turn.capture) {
        const m = turn.capture.exec(stdout);
        if (m) store.updateSession(ud, id, { cliSession: m[1] });
      } else if (!stopped && turn.listArgs) {
        const sid = agentCli.pickListedSession(await capture(turn.listArgs, repo), repo, startedAt);
        if (sid) store.updateSession(ud, id, { cliSession: sid });
      }
    } catch { /* ID が拾えなくても次のターンは履歴の再送で続く */ }
    const message = {
      role: 'assistant',
      text: answer || (stopped ? '（停止した）' : `（応答なし。終了コード ${code}）`),
      code, elapsedMs: Date.now() - startedAt, stopped,
      // 失敗の理由は定義の errors が分類できればその hint、できなければ末尾数行（認証切れのように stdout へ出す CLI もある）
      error: !failed ? '' : (rule ? rule.hint : (stderr.trim() || stdout.trim()).split('\n').slice(-6).join('\n')),
    };
    try { store.appendMessage(ud, id, message); } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    send('turn:done', { id, message });
  });
  return { pid: child.pid, argv: turn.argv };
}

function registerIpcHandlers(getWindow) {
  const send = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };

  handle('config:get', () => store.loadConfig(userData()));
  handle('config:save', (p) => store.saveConfig(userData(), p.patch));

  handle('repo:add', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: 'リポジトリを登録する' });
    if (res.canceled || !res.filePaths.length) return null;
    return store.addRepo(userData(), res.filePaths[0]);
  });
  handle('repo:remove', (p) => store.removeRepo(userData(), p.repo));
  handle('agents:list', (p) => agentCli.list(p.repo ? requireRepo(p.repo) : ''));

  handle('session:list', (p) => store.listSessions(userData(), p.repo || ''));
  handle('session:create', (p) => store.createSession(userData(), { ...p, repo: requireRepo(p.repo) }));
  handle('session:read', (p) => store.readSession(userData(), p.id));
  handle('session:update', (p) => store.updateSession(userData(), p.id, p.patch));
  handle('session:remove', (p) => {
    if (running.has(p.id)) running.get(p.id).stop();
    return store.removeSession(userData(), p.id);
  });

  handle('turn:send', (p) => runTurn(p.id, p.prompt, send));
  handle('turn:stop', (p) => { const c = running.get(p.id); if (c) c.stop(); return !!c; });
  handle('turn:running', () => [...running.keys()]);

  handle('git:changes', (p) => git.changes(requireRepo(p.repo)));
  handle('git:file', (p) => git.fileDiff(requireRepo(p.repo), String(p.file || '')));
  handle('shell:openFolder', (p) => shell.openPath(requireRepo(p.repo)));

  app.on('before-quit', () => { for (const c of running.values()) c.stop(); });
}

module.exports = { registerIpcHandlers, spawnSpec, lineEmitter, stripAnsi, cleanAnswer };
