'use strict';

const { ipcMain, dialog, shell, app } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const agentCli = require('./agentCli');
const store = require('./store');
const git = require('./git');
const files = require('./files');
const host = require('./host');
const tmux = require('./tmux');
const { stripAnsi, cleanAnswer, lineEmitter } = require('./text');

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

function distroFor(repo) {
  return host.hostOf(repo, store.loadConfig(userData()).wslDistro).distro;
}

// ---- ヘッドレス（1 ターン 1 プロセス）。tmux が無いときの代替 --------------------------

// Windows では CLI は WSL に居るので wsl.exe -e bash -lc に載せる（cwd も WSL 表記へ）。
// Linux / macOS はそのまま起動する。
function spawnSpec(command, args, { cwd = '', env = {}, distro = '' } = {}) {
  if (process.platform !== 'win32') return { command, args, extra: { cwd, env: { ...process.env, ...env }, detached: true } };
  const exportsStr = Object.entries(env).map(([k, v]) => `export ${k}=${host.sq(v)};`).join(' ');
  const script = `${exportsStr} cd ${host.sq(host.toHostPath(cwd))} && exec ${host.quoteArgv([command, ...args])}`;
  return {
    command: 'wsl.exe',
    args: [...(distro ? ['-d', distro] : []), '-e', 'bash', '-lc', script],
    extra: { windowsHide: true },
  };
}

function capture(argv, cwd) {
  return new Promise((resolve) => {
    execFile(argv[0], argv.slice(1), { cwd, windowsHide: true, timeout: 30000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => resolve(err && !stdout ? '' : String(stdout || '')));
  });
}

// 走っているヘッドレスのターン。セッション ID → 子プロセス。
const running = new Map();

function killTree(child) {
  try {
    if (process.platform === 'win32') execFile('taskkill', ['/pid', String(child.pid), '/T', '/F'], () => {});
    else process.kill(-child.pid, 'SIGTERM');
  } catch {
    try { child.kill(); } catch { /* 既に終わっている */ }
  }
}

function runHeadless(id, prompt, send) {
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
  const spec2 = spawnSpec(turn.command, turn.args, { cwd: repo, env: turn.env, distro: distroFor(repo) });
  let child;
  try {
    child = spawn(spec2.command, spec2.args, { windowsHide: true, ...spec2.extra });
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

// ---- tmux（対話起動）。会話 ID → Conversation ----------------------------------------

const conversations = new Map();

async function openConversation(id, send, { cols, rows, fresh = false } = {}) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const cfg = store.loadConfig(ud);
  const existing = conversations.get(id);
  if (existing && !fresh) {
    if (cols && rows) await existing.resize(cols, rows);
    return { name: existing.name, phase: existing.phase, detail: existing.detail, reused: true, warning: '' };
  }
  const spec = agentCli.load(sess.cli, repo);
  const { distro, cwd, shell } = host.hostOf(repo, cfg.wslDistro);
  const info = await host.probe(distro);
  if (!info.ok) throw new Error(info.error || 'ホストのシェルを起動できません');
  if (!info.tmux) throw new Error(process.platform === 'win32' ? 'WSL に tmux が見つかりません（sudo apt install tmux）' : 'tmux が見つかりません');
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const launch = agentCli.interactiveCmd(spec, { model: sess.model, readonly: sess.readonly, cliSession: sess.cliSession, history });
  const conv = new tmux.Conversation({
    id, shell, cwd, argv: launch.argv, patterns: tmux.compilePatterns(spec.interactive), cols, rows,
    emit: (channel, payload) => send(channel, payload),
  });
  if (existing) { await existing.kill(); conversations.delete(id); }
  conversations.set(id, conv);
  let opened;
  try {
    opened = await conv.open({ reuse: !fresh });
  } catch (err) {
    conversations.delete(id);
    throw err;
  }
  if (!opened.reused && launch.mintedSession) store.updateSession(ud, id, { cliSession: launch.mintedSession });
  const warning = [launch.readonlyWarning, opened.reused ? '' : launch.warning].filter(Boolean).join('\n');
  return { name: conv.name, phase: conv.phase, detail: conv.detail, reused: opened.reused, warning, argv: launch.argv };
}

async function runTmux(id, prompt, send) {
  const ud = userData();
  const text = String(prompt || '').trim();
  if (!text) throw new Error('依頼が空です');
  let conv = conversations.get(id);
  if (!conv || conv.closed) { await openConversation(id, send); conv = conversations.get(id); }
  if (conv.turn) throw new Error('このセッションは応答中です');
  await conv.waitReady();
  store.appendMessage(ud, id, { role: 'user', text });
  await conv.send(text, (message) => {
    try { store.appendMessage(ud, id, message); } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    send('turn:done', { id, message });
  });
  send('turn:started', { id, argv: [], warning: '' });
  return { name: conv.name };
}

// 起動中なら入力受付まで待つ（定義の ready_timeout_sec）。
tmux.Conversation.prototype.waitReady = function waitReady() {
  const limit = this.patterns.readyTimeoutSec * 1000 + 2000;
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (this.phase === 'dead' || this.phase === 'gone') return reject(new Error(this.detail || 'CLI が終了しています'));
      if (this.phase !== 'starting') return resolve();
      if (Date.now() - start > limit) return resolve();      // 検出できないまま送る（画面で分かる）
      return setTimeout(tick, 200);
    };
    tick();
  });
};

// ---- 登録 ------------------------------------------------------------------------

// 定義ごとの「使える」印はホスト側の PATH で引く（Windows では WSL の中）。
const availCache = new Map();
async function hostAvailability(distro, commands) {
  const key = `${distro}|${commands.join(',')}`;
  const hit = availCache.get(key);
  if (hit && Date.now() - hit.at < 60000) return hit.map;
  const sh = host.shellFor(distro);
  const script = `for c in ${commands.map(host.sq).join(' ')}; do printf '%s=%s\\n' "$c" "$(command -v "$c" 2>/dev/null || true)"; done`;
  const r = await sh.run(script, { timeoutMs: 20000 });
  const map = new Map();
  if (r.ok) for (const line of r.output.split('\n')) { const m = line.match(/^([^=]+)=(.*)$/); if (m) map.set(m[1], m[2].trim()); }
  availCache.set(key, { at: Date.now(), map });
  return r.ok ? map : null;
}

async function listAgents(repo) {
  const defs = agentCli.list(repo);
  const distro = repo ? distroFor(repo) : store.loadConfig(userData()).wslDistro;
  const map = await hostAvailability(distro, [...new Set(defs.map((d) => d.command))]);
  if (!map) return defs;                                     // ホストに聞けない → ローカル PATH の判定のまま
  return defs.map((d) => ({ ...d, available: !!map.get(d.command) }));
}

function registerIpcHandlers(getWindow) {
  const send = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };

  handle('host:info', async () => {
    const cfg = store.loadConfig(userData());
    const info = await host.probe(process.platform === 'win32' ? cfg.wslDistro : '');
    return { platform: process.platform, distro: cfg.wslDistro, ...info, socket: tmux.SOCKET };
  });
  handle('config:get', () => store.loadConfig(userData()));
  handle('config:save', (p) => {
    const before = store.loadConfig(userData());
    const next = store.saveConfig(userData(), p.patch);
    if (before.wslDistro !== next.wslDistro) { host.closeAll(); availCache.clear(); }
    return next;
  });

  handle('repo:add', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: 'リポジトリを登録する' });
    if (res.canceled || !res.filePaths.length) return null;
    return store.addRepo(userData(), res.filePaths[0]);
  });
  handle('repo:remove', (p) => store.removeRepo(userData(), p.repo));
  handle('agents:list', (p) => listAgents(p.repo ? requireRepo(p.repo) : ''));

  handle('session:list', (p) => store.listSessions(userData(), p.repo || ''));
  handle('session:create', (p) => store.createSession(userData(), { ...p, repo: requireRepo(p.repo) }));
  handle('session:read', (p) => store.readSession(userData(), p.id));
  handle('session:update', (p) => store.updateSession(userData(), p.id, p.patch));
  handle('session:remove', async (p) => {
    if (running.has(p.id)) running.get(p.id).stop();
    const conv = conversations.get(p.id);
    if (conv) { conversations.delete(p.id); await conv.kill(); }
    return store.removeSession(userData(), p.id);
  });

  handle('turn:send', (p) => {
    const sess = store.readSession(userData(), p.id);
    return sess.transport === 'tmux' ? runTmux(p.id, p.prompt, send) : runHeadless(p.id, p.prompt, send);
  });
  handle('turn:stop', async (p) => {
    const c = running.get(p.id);
    if (c) { c.stop(); return true; }
    const conv = conversations.get(p.id);
    return conv ? conv.stop() : false;
  });
  handle('turn:running', () => [...running.keys(), ...[...conversations.values()].filter((c) => c.turn).map((c) => c.id)]);

  // 端末（tmux）
  handle('term:open', (p) => openConversation(p.id, send, { cols: p.cols, rows: p.rows }));
  handle('term:restart', (p) => openConversation(p.id, send, { cols: p.cols, rows: p.rows, fresh: true }));
  handle('term:state', (p) => {
    const conv = conversations.get(p.id);
    return conv ? { name: conv.name, phase: conv.phase, detail: conv.detail, busy: !!conv.turn } : null;
  });
  handle('term:watch', (p) => { const c = conversations.get(p.id); if (c) c.watch(); return !!c; });
  handle('term:unwatch', (p) => { const c = conversations.get(p.id); if (c) c.unwatch(); return !!c; });
  handle('term:keys', (p) => { const c = conversations.get(p.id); if (!c) throw new Error('端末が開いていない'); return c.keys(String(p.data || '')); });
  handle('term:resize', (p) => { const c = conversations.get(p.id); return c ? c.resize(p.cols, p.rows) : false; });
  handle('term:kill', async (p) => { const c = conversations.get(p.id); if (!c) return false; conversations.delete(p.id); await c.kill(); return true; });

  // リポジトリのファイル
  handle('fs:list', (p) => files.listDir(requireRepo(p.repo), p.rel || ''));
  handle('fs:read', (p) => files.readFile(requireRepo(p.repo), p.rel || ''));
  handle('fs:find', (p) => files.find(requireRepo(p.repo), p.query || '', 200));

  handle('git:changes', (p) => { const repo = requireRepo(p.repo); return git.changes(repo, distroFor(repo)); });
  handle('git:file', (p) => { const repo = requireRepo(p.repo); return git.fileDiff(repo, String(p.file || ''), distroFor(repo)); });
  handle('shell:openFolder', (p) => shell.openPath(requireRepo(p.repo)));
  handle('shell:openFile', (p) => {
    const repo = requireRepo(p.repo);
    const { target } = files.resolveInside(repo, p.rel || '');
    return shell.openPath(target);
  });
  handle('shell:showFile', (p) => {
    const repo = requireRepo(p.repo);
    const { target } = files.resolveInside(repo, p.rel || '');
    shell.showItemInFolder(path.normalize(target));
    return true;
  });

  app.on('before-quit', () => {
    for (const c of running.values()) c.stop();
    for (const c of conversations.values()) c.detach();     // tmux セッションは残す（次回に再接続する）
    host.closeAll();
  });
}

module.exports = { registerIpcHandlers, spawnSpec, lineEmitter, stripAnsi, cleanAnswer };
