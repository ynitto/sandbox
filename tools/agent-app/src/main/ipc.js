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
const worktree = require('./worktree');
const attachments = require('./attachments');
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

// 作業フォルダ。画面から受け取るのは worktree の**名前**だけで、生のパスは受け取らない
// （名前は worktree.checkName が形を検査するので `..` を持ち込めない）。
function dirsOf(repo, name, { mustExist = false } = {}) {
  const dirs = worktree.dirsFor(requireRepo(repo), name || '');
  if (mustExist && dirs.name) {
    let st;
    try { st = fs.statSync(dirs.fsDir); } catch { st = null; }
    if (!st || !st.isDirectory()) {
      throw new Error(`作業フォルダが見つかりません: ${worktree.SUBDIR}/${dirs.name}（この画面の外で消された可能性があります）`);
    }
  }
  return dirs;
}

function sessionDirs(sess) {
  return dirsOf(sess.repo, sess.worktree || '');
}

// 「ブランチ全体」の差分の分岐元。登録したフォルダ（＝ふつうは本体の worktree）の今のブランチ。
async function mainBranch(repo, distro) {
  const r = await host.shellFor(distro).exec(['git', '-C', host.toHostPath(repo), 'rev-parse', '--abbrev-ref', 'HEAD'], { timeoutMs: 20000 });
  const name = r.ok ? r.output.trim() : '';
  return name && name !== 'HEAD' ? name : '';
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

// 1 ターンの起動条件。画面からターンごとに届く（無ければ会話の「次のターン」の既定）。
//   cli / model / readonly … このターンで使う CLI・モデル・モード
//   text                   … 利用者が書いた依頼（画面に出す・保存する本文）
//   prompt                 … CLI に渡す本文（依頼 + 添付ファイルの案内）
//   atts                   … 添付 [{ id, name, size } | { rel, name }]
//   files                  … 添付の実体（ホスト側のパス。file_flag を持つ CLI にだけ argv でも渡す）
function turnSpec(sess, p) {
  const cli = String(p.cli || sess.cli || '').trim().toLowerCase();
  const model = String(p.model != null ? p.model : sess.model || '').trim();
  const readonly = p.readonly != null ? Boolean(p.readonly) : Boolean(sess.readonly);
  const text = String(p.prompt || '').trim();
  if (!text && !(p.attachments || []).length) throw new Error('依頼が空です');
  return { cli, model, readonly, text };
}

// 添付ファイルを確かめ、依頼文の末尾に「どこにあるか」を添える。
//   { id, name } … userData の attachments/<id>/<name>（ホスト側のパスで伝える）
//   { rel }      … 作業フォルダの中のファイル（相対パスのまま伝える。写さない）
function withAttachments(ud, text, list, dirs) {
  const atts = [];
  const paths = [];
  const lines = [];
  for (const a of (Array.isArray(list) ? list : []).slice(0, attachments.MAX_PER_TURN)) {
    if (!a || typeof a !== 'object') continue;
    if (a.id) {
      const { path: file, size } = attachments.resolve(ud, a.id, a.name);
      const hostPath = host.toHostPath(file);
      atts.push({ id: attachments.checkId(a.id), name: attachments.safeName(a.name), size });
      paths.push(hostPath);
      lines.push(`- ${hostPath}`);
    } else if (a.rel) {
      const { rel } = files.resolveInside(dirs.fsDir, String(a.rel));
      atts.push({ rel, name: rel.split('/').pop() });
      lines.push(`- ${rel}（作業フォルダの中）`);
    }
  }
  const body = String(text || '');
  if (!lines.length) return { prompt: body, atts, files: paths };
  const note = `添付ファイル（必要に応じて読んで参照すること）:\n${lines.join('\n')}`;
  return { prompt: body ? `${body}\n\n${note}` : note, atts, files: paths };
}

function runHeadless(id, turn, send) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const dirs = dirsOf(sess.repo, sess.worktree || '', { mustExist: true });
  if (running.has(id)) throw new Error('このセッションは応答中です');
  const { cli, model, readonly, text, prompt, atts, files: attFiles, spec } = turn;
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const entry = store.cliEntry(sess, cli);
  // その CLI がまだ見ていない分だけ再送する（セッション ID が無い CLI は毎回ぜんぶ）
  const unseen = entry && entry.id ? history.slice(entry.seen) : history;
  const cmd = agentCli.turnCmd(spec, {
    prompt, model, readonly, cliSession: entry ? entry.id : '', history: unseen, files: attFiles,
  });
  store.appendMessage(ud, id, { role: 'user', text, cli, model, readonly, attachments: atts });
  if (cmd.mintedSession) store.setCliEntry(ud, id, cli, { id: cmd.mintedSession });

  const startedAt = Date.now();
  const spec2 = spawnSpec(cmd.command, cmd.args, { cwd: dirs.fsDir, env: cmd.env, distro: distroFor(repo) });
  let child;
  try {
    child = spawn(spec2.command, spec2.args, { windowsHide: true, ...spec2.extra });
  } catch (err) {
    throw new Error(`起動できません: ${(err && err.message) || err}`);
  }
  running.set(id, child);
  send('turn:started', { id, argv: cmd.argv, warning: cmd.readonlyWarning });

  let stdout = '';
  let stderr = '';
  let stopped = false;
  child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
  child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
  child.stdout.on('data', lineEmitter((line) => send('turn:line', { id, kind: 'stdout', text: line })));
  child.stderr.on('data', lineEmitter((line) => send('turn:line', { id, kind: 'stderr', text: line })));
  child.on('error', (err) => { stderr += `\n起動エラー: ${(err && err.message) || err}`; });
  child.stdin.on('error', () => { /* 先に終わった CLI へ書いた EPIPE */ });
  child.stdin.end(cmd.stdin == null ? '' : cmd.stdin);
  child.stop = () => { stopped = true; killTree(child); };

  child.on('close', async (code) => {
    running.delete(id);
    let answer = '';
    if (cmd.outputFile) {
      try { answer = fs.readFileSync(cmd.outputFile, 'utf8'); } catch { /* 書かれなかった */ }
      try { fs.unlinkSync(cmd.outputFile); } catch { /* 無ければよい */ }
    } else {
      answer = stdout;
    }
    answer = cleanAnswer(answer);
    stderr = stripAnsi(stderr);
    const failed = stopped || code !== 0 || !answer;
    const rule = failed ? agentCli.classifyError(spec, `${stderr}\n${stdout}`) : null;
    let sid = cmd.mintedSession || (entry ? entry.id : '');
    try {
      if (!stopped && cmd.capture) {
        const m = cmd.capture.exec(stdout);
        if (m) sid = m[1];
      } else if (!stopped && cmd.listArgs) {
        sid = agentCli.pickListedSession(await capture(cmd.listArgs, dirs.fsDir), dirs.hostDir, startedAt) || sid;
      }
    } catch { /* ID が拾えなくても次のターンは履歴の再送で続く */ }
    const message = {
      role: 'assistant', cli, model,
      text: answer || (stopped ? '（停止した）' : `（応答なし。終了コード ${code}）`),
      code, elapsedMs: Date.now() - startedAt, stopped,
      // 失敗の理由は定義の errors が分類できればその hint、できなければ末尾数行（認証切れのように stdout へ出す CLI もある）
      error: !failed ? '' : (rule ? rule.hint : (stderr.trim() || stdout.trim()).split('\n').slice(-6).join('\n')),
    };
    try {
      const saved = store.appendMessage(ud, id, message);
      // セッション ID が分かる CLI は、ここまでのやり取りをその CLI が見たものとして覚える
      if (sid) store.setCliEntry(ud, id, cli, { id: sid, seen: saved.messages.length });
    } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    send('turn:done', { id, message });
  });
  return { pid: child.pid, argv: cmd.argv };
}

// ---- tmux（対話起動）。会話 ID → Conversation ----------------------------------------

const conversations = new Map();

function sameLaunch(a, b) {
  return !!a && !!b && a.cli === b.cli && String(a.model || '') === String(b.model || '') && Boolean(a.readonly) === Boolean(b.readonly);
}

// tmux セッションを（無ければ起動して）持つ。
//   launch … { cli, model, readonly }。ターンが指定する。動いているもの（existing / sess.live）と
//            違えば起動し直す（モデルやエージェントを変えたターン）
//   fresh  … 残っているセッションを消して、会話の「次のターン」の既定で起動し直す（「再起動」）
//   どちらも無し（会話を開いただけ）… 動いているものにつなぐだけで、起動し直さない
async function openConversation(id, send, { cols, rows, fresh = false, launch = null } = {}) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const cfg = store.loadConfig(ud);
  const defaults = { cli: sess.cli, model: sess.model || '', readonly: !!sess.readonly };
  const existing = conversations.get(id);
  const live = existing ? existing.launch : sess.live;
  const want = launch || (fresh ? defaults : (live || defaults));
  const restart = fresh || (!!launch && !!live && !sameLaunch(live, launch));
  if (existing && !restart) {
    if (cols && rows) await existing.resize(cols, rows);
    return { name: existing.name, phase: existing.phase, detail: existing.detail, reused: true, restarted: false, warning: '', launch: existing.launch };
  }
  const spec = agentCli.load(want.cli, repo);
  if (!spec.interactive) throw new Error(`${want.cli} は対話起動（interactive）の定義を持ちません`);
  const { distro, shell } = host.hostOf(repo, cfg.wslDistro);
  const cwd = dirsOf(sess.repo, sess.worktree || '', { mustExist: true }).hostDir;
  const info = await host.probe(distro);
  if (!info.ok) throw new Error(info.error || 'ホストのシェルを起動できません');
  if (!info.tmux) throw new Error(process.platform === 'win32' ? 'WSL に tmux が見つかりません（sudo apt install tmux）' : 'tmux が見つかりません');
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const entry = store.cliEntry(sess, want.cli);
  const cmd = agentCli.interactiveCmd(spec, { model: want.model, readonly: want.readonly, cliSession: entry ? entry.id : '', history });
  const conv = new tmux.Conversation({
    id, shell, cwd, argv: cmd.argv, patterns: tmux.compilePatterns(spec.interactive), cols, rows, launch: want,
    emit: (channel, payload) => send(channel, payload),
  });
  if (existing) { conv.watchers = existing.watchers; existing.detach(); conversations.delete(id); }
  conversations.set(id, conv);
  let opened;
  try {
    opened = await conv.open({ reuse: !restart });
  } catch (err) {
    conversations.delete(id);
    throw err;
  }
  if (opened.reused) {
    // 生きていたセッションは、この会話のここまでを全部見ている（別の CLI へ移るときは消しているので）
    conv.resumed = true;
    conv.seen = history.length;
  } else {
    conv.resumed = cmd.resumed;
    conv.seen = cmd.resumed && entry ? entry.seen : 0;
    if (cmd.mintedSession) store.setCliEntry(ud, id, want.cli, { id: cmd.mintedSession, seen: 0 });
  }
  store.updateSession(ud, id, { live: want, transport: 'tmux' });
  const warning = [cmd.readonlyWarning, opened.reused ? '' : cmd.warning].filter(Boolean).join('\n');
  return { name: conv.name, phase: conv.phase, detail: conv.detail, reused: opened.reused, restarted: !opened.reused, warning, argv: cmd.argv, launch: want };
}

async function closeConversation(id) {
  const conv = conversations.get(id);
  if (conv) { conversations.delete(id); await conv.kill(); }
  else {
    // 追跡していない（起動し直したアプリ）tmux セッションも消す
    const sess = store.readSession(userData(), id);
    if (sess.live) {
      const { shell } = host.hostOf(sess.repo, store.loadConfig(userData()).wslDistro);
      await shell.run(tmux.cmdKill(tmux.sessionName(id)));
    }
  }
  store.updateSession(userData(), id, { live: null });
}

async function runTmux(id, turn, send) {
  const ud = userData();
  const { cli, model, readonly, text, prompt, atts } = turn;
  const want = { cli, model, readonly };
  let conv = conversations.get(id);
  let opened = null;
  if (conv && conv.turn) throw new Error('このセッションは応答中です');
  // 無い・終わっている・起動条件が違う → 起動（し直す）。モデルやエージェントの変更はここで効く
  if (!conv || conv.closed || conv.phase === 'dead' || conv.phase === 'gone' || !sameLaunch(conv.launch, want)) {
    const fresh = !!conv && (conv.phase === 'dead' || conv.phase === 'gone');
    opened = await openConversation(id, send, { fresh, launch: want });
    conv = conversations.get(id);
  }
  try {
    await conv.waitReady();
  } catch (err) {
    // つなぎ直した先の CLI が終わっていた（アプリを閉じている間に落ちた等）→ 起動し直して続ける
    if (conv.phase !== 'dead' && conv.phase !== 'gone') throw err;
    opened = await openConversation(id, send, { fresh: true, launch: want });
    conv = conversations.get(id);
    await conv.waitReady();
  }
  const sess = store.readSession(ud, id);
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const unseen = history.slice(conv.seen);
  const full = unseen.length ? agentCli.replayPrompt(unseen, prompt, { resumed: conv.resumed }) : prompt;
  store.appendMessage(ud, id, { role: 'user', text, cli, model, readonly, attachments: atts });
  await conv.send(full, (message) => {
    try {
      const saved = store.appendMessage(ud, id, { ...message, cli, model });
      conv.seen = saved.messages.length;
      store.setCliEntry(ud, id, cli, { seen: saved.messages.length });
    } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    send('turn:done', { id, message });
  });
  send('turn:started', { id, argv: [], warning: opened ? opened.warning : '' });
  return { name: conv.name, restarted: !!(opened && opened.restarted), warning: opened ? opened.warning : '' };
}

// 1 ターン。CLI・モデル・モードはターンごとに決め、tmux か ヘッドレスかもここで決める
// （対話定義を持つ CLI で tmux が使えるなら tmux）。
async function runTurn(id, p, send) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const dirs = dirsOf(sess.repo, sess.worktree || '', { mustExist: true });
  const base = turnSpec(sess, p);
  const spec = agentCli.load(base.cli, repo);
  const cfg = store.loadConfig(ud);
  let transport = 'headless';
  if (cfg.transport === 'tmux' && spec.interactive) {
    const info = await host.probe(distroFor(repo));
    if (info.ok && info.tmux) transport = 'tmux';
  }
  const { prompt, atts, files: attFiles } = withAttachments(ud, base.text, p.attachments, dirs);
  const turn = { ...base, prompt, atts, files: attFiles, spec };
  // 次のターンの既定として覚える（画面はこれを出す）
  store.updateSession(ud, id, { cli: base.cli, model: base.model, readonly: base.readonly, transport });
  if (transport === 'headless') {
    // ヘッドレスの CLI へ移るなら、動いていた tmux の CLI は止める（同時に 2 つは持たない）
    if (conversations.has(id) || sess.live) await closeConversation(id);
    return runHeadless(id, turn, send);
  }
  return runTmux(id, turn, send);
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
  // 写したが送らずに閉じた添付を掃除する
  try { attachments.sweep(userData(), store.readAllSessions(userData())); } catch { /* 消せなくても動く */ }

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
  handle('session:create', async (p) => {
    const repo = requireRepo(p.repo);
    let branch = '';
    if (p.worktree) branch = (await worktree.find(repo, p.worktree, distroFor(repo))).branch;
    return store.createSession(userData(), { ...p, repo, branch });
  });
  handle('session:read', (p) => store.readSession(userData(), p.id));
  handle('session:update', (p) => store.updateSession(userData(), p.id, p.patch));
  handle('session:remove', async (p) => {
    if (running.has(p.id)) running.get(p.id).stop();
    const conv = conversations.get(p.id);
    if (conv) { conversations.delete(p.id); await conv.kill(); }
    try { attachments.discardAll(userData(), store.readSession(userData(), p.id)); } catch { /* 会話が読めなければ添付も辿れない */ }
    return store.removeSession(userData(), p.id);
  });

  handle('turn:send', (p) => runTurn(p.id, p, send));

  // 添付ファイル
  handle('attach:pick', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openFile', 'multiSelections'], title: '添付するファイル' });
    if (res.canceled || !res.filePaths.length) return [];
    return res.filePaths.slice(0, attachments.MAX_PER_TURN).map((f) => attachments.stageFile(userData(), f));
  });
  handle('attach:stage', (p) => {
    const bytes = p.bytes;
    if (!(bytes instanceof Uint8Array) && !Buffer.isBuffer(bytes) && !(bytes instanceof ArrayBuffer)) throw new Error('添付の中身が読めません');
    return attachments.stage(userData(), p.name, bytes instanceof ArrayBuffer ? Buffer.from(bytes) : Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength));
  });
  handle('attach:discard', (p) => attachments.discard(userData(), p.id));
  handle('attach:open', (p) => shell.openPath(attachments.resolve(userData(), p.id, p.name).path));
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
  handle('term:kill', async (p) => { const had = conversations.has(p.id); await closeConversation(p.id); return had; });

  // 作業フォルダ（git worktree）
  handle('wt:list', (p) => { const repo = requireRepo(p.repo); return worktree.list(repo, distroFor(repo)); });
  handle('wt:create', async (p) => {
    const repo = requireRepo(p.repo);
    return worktree.create(repo, { branch: p.branch, base: p.base, name: p.name }, distroFor(repo));
  });
  handle('wt:remove', async (p) => {
    const repo = requireRepo(p.repo);
    const name = worktree.checkName(p.name);
    // その作業フォルダで動いている会話の tmux セッションを先に止める（開いたままだと git が断る）
    const ud = userData();
    for (const s of store.listSessions(ud, repo)) {
      if (s.worktree !== name) continue;
      if (running.has(s.id)) running.get(s.id).stop();
      const conv = conversations.get(s.id);
      if (conv) { conversations.delete(s.id); await conv.kill(); }
    }
    return worktree.remove(repo, name, { force: !!p.force, deleteBranch: !!p.deleteBranch, forceBranch: !!p.forceBranch }, distroFor(repo));
  });

  // リポジトリのファイル（作業フォルダの中を見る）
  handle('fs:list', (p) => files.listDir(dirsOf(p.repo, p.worktree).fsDir, p.rel || ''));
  handle('fs:read', (p) => files.readFile(dirsOf(p.repo, p.worktree).fsDir, p.rel || ''));
  handle('fs:find', (p) => files.find(dirsOf(p.repo, p.worktree).fsDir, p.query || '', 200));

  handle('git:changes', async (p) => {
    const repo = requireRepo(p.repo);
    const distro = distroFor(repo);
    const dirs = dirsOf(repo, p.worktree, { mustExist: true });
    const scope = p.scope === 'branch' ? 'branch' : 'worktree';
    const base = scope === 'branch' && dirs.name ? await mainBranch(repo, distro) : '';
    return git.changes(dirs.hostDir, distro, { scope, base });
  });
  handle('git:file', async (p) => {
    const repo = requireRepo(p.repo);
    const distro = distroFor(repo);
    const dirs = dirsOf(repo, p.worktree, { mustExist: true });
    const scope = p.scope === 'branch' ? 'branch' : 'worktree';
    const base = scope === 'branch' && dirs.name ? await mainBranch(repo, distro) : '';
    return git.fileDiff(dirs.hostDir, String(p.file || ''), distro, { scope, base });
  });
  handle('shell:openFolder', (p) => shell.openPath(dirsOf(p.repo, p.worktree, { mustExist: true }).fsDir));
  handle('shell:openFile', (p) => {
    const { target } = files.resolveInside(dirsOf(p.repo, p.worktree).fsDir, p.rel || '');
    return shell.openPath(target);
  });
  handle('shell:showFile', (p) => {
    const { target } = files.resolveInside(dirsOf(p.repo, p.worktree).fsDir, p.rel || '');
    shell.showItemInFolder(path.normalize(target));
    return true;
  });

  app.on('before-quit', () => {
    for (const c of running.values()) c.stop();
    for (const c of conversations.values()) c.detach();     // tmux セッションは残す（次回に再接続する）
    host.closeAll();
  });
}

module.exports = { registerIpcHandlers, spawnSpec, lineEmitter, stripAnsi, cleanAnswer, withAttachments, turnSpec, sameLaunch };
