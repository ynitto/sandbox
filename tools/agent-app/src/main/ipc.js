'use strict';

const { ipcMain, dialog, shell, app } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const agentCli = require('./agentCli');
const store = require('./store');
const git = require('./git');
const files = require('./files');
const host = require('./host');
const tmux = require('./tmux');
const worktree = require('./worktree');
const attachments = require('./attachments');
const settings = require('./settings');
const sessionSetup = require('./sessionSetup');
const forkProtocol = require('../renderer/forkProtocol');
const response = require('./response');
const { createGate } = require('./executionGate');
const skills = require('./skills');
const skillSelection = require('./skillSelection');
const herd = require('./herd');
const agentsMod = require('./agents');
const share = require('./share');
const { registerAutomationIpc } = require('./automation/ipc');
const automationTools = require('./automation/tools');
const machineStore = require('./automation/store');
const teaching = require('./automation/teaching');
const recordingBrowser = require('./automation/browser');
const { stripAnsi, cleanAnswer, lineEmitter } = require('./text');

function userData() { return app.getPath('userData'); }

// 修正前に保存された Aider 応答も、読み出し時に同じ表示契約へ移す。
// ディスク上の生データは変更せず、新しい応答は保存前に既に構造化される。
// 分岐の関連づけ（origin の会話の題名と、この会話から分岐した会話）は表示のたびに一覧から引く。
function presentSession(sess, ud = null) {
  if (!sess || !Array.isArray(sess.messages)) return sess;
  let originSession = null;
  let forks = [];
  if (sess.id) {
    try {
      const dir = ud || userData();
      if (sess.origin) {
        const o = store.readSession(dir, sess.origin.sessionId);
        originSession = { id: o.id, repo: o.repo, title: o.title || '' };
      }
      forks = store.listForks(dir, sess.id).map((f) => ({ id: f.id, repo: f.repo, title: f.title || '', index: f.origin ? f.origin.index : -1 }));
    } catch { /* 分岐元が消えていても会話は開ける */ }
  }
  return {
    ...sess,
    originSession, forks,
    messages: sess.messages.map((message) => {
      if (!message || message.role !== 'assistant') return message;
      const structured = response.parseTranscript(message.cli, message.text);
      if (!structured.thinking.length) return message;
      const parts = message.parts && typeof message.parts === 'object' ? message.parts : {};
      const currentThinking = Array.isArray(parts.thinking) ? parts.thinking : [];
      return {
        ...message,
        text: structured.text,
        parts: { ...parts, thinking: currentThinking.length ? currentThinking : structured.thinking },
      };
    }),
  };
}

// すべてのハンドラを {ok, data|error} に揃える。
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, args) => {
    try {
      return { ok: true, data: await fn(args || {}, event) };
    } catch (err) {
      return {
        ok: false,
        error: err && err.message ? err.message : String(err),
        ...(err && err.code ? { code: err.code } : {}),
        ...(err && err.detail ? { detail: err.detail } : {}),
        ...(err && err.issues ? { issues: err.issues } : {}),
      };
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
  const wsl = host.wslArgv(command, args, { cwd, env, distro });
  return { command: wsl.command, args: wsl.args, extra: { windowsHide: true } };
}

function capture(argv, cwd) {
  return new Promise((resolve) => {
    execFile(argv[0], argv.slice(1), { cwd, windowsHide: true, timeout: 30000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => resolve(err && !stdout ? '' : String(stdout || '')));
  });
}

// 走っているヘッドレスのターン。セッション ID → 子プロセス。
const running = new Map();
const turnGate = createGate();
const instanceId = crypto.randomUUID();

function emitResponseParts(send, id, added) {
  for (const item of (added && added.thinking) || []) send('turn:progress', { id, item });
  for (const item of (added && added.information) || []) send('turn:info', { id, item });
}

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
  const autoApprove = p.autoApprove != null ? Boolean(p.autoApprove) : Boolean(sess.autoApprove);
  const text = String(p.prompt || '').trim();
  if (!text && !(p.attachments || []).length) throw new Error('依頼が空です');
  return { cli, model, readonly, autoApprove, text };
}

// 保存済みの起動方針を、そのターンで実際に使う CLI / model へ解決する。
// policy を持たない旧画面・旧セッションは、それまでの直接指定の意味を保つ。
//   optimized … 「エージェントを最適化する」が効いているか（設定 × herd の有無）。false なら節約 /
//               品質重視は「おすすめ」として解決する（settings.effectivePolicy）
function executionSpec(sess, p, config, { optimized = true } = {}) {
  const legacyDirect = !p.policy;
  const selected = settings.resolve(config, legacyDirect ? {
    policy: 'direct', cli: p.cli || sess.cli, model: p.model != null ? p.model : sess.model,
  } : p, { optimized });
  const base = turnSpec(sess, { ...p, cli: selected.cli, model: selected.model });
  return { ...base, policy: selected.policy, tier: selected.tier, source: selected.source };
}

// `herd`（一族の 1 語）を、このターンで実際に起こす定義へ写す。requested に元の名前を残す。
// 起こすのは常に一族の共通 TUI（agent-herd の既定バックエンド）で、用途は本文の先頭に置く
// スラッシュ行（slash）で表す——ターンごとに CLI を入れ替えない。
//   agents      … listAgents の結果（ホストで使えるかの印つき）
//   attachments … 添付（作業フォルダの中のファイルがあれば /edit）
function concreteCli(spec, agents, { attachments = [] } = {}) {
  if (!herd.isHerd(spec.cli)) return { ...spec, requested: spec.cli, family: '', slash: '' };
  const purpose = herd.purposeOf({ readonly: spec.readonly, workFiles: herd.hasWorkFiles(attachments) });
  const picked = herd.resolveChat(purpose, agents);
  return { ...spec, cli: picked.cli, requested: herd.HERD, family: herd.HERD, slash: picked.slash, familyReason: picked.reason };
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
  const { cli, model, readonly, autoApprove, text, atts, files: attFiles, spec, policy, tier, selectedSkills, family = '', slash = '' } = turn;
  const prompt = herd.withSlash(slash, turn.prompt);          // 用途のスラッシュ行は本文の一番上
  const collector = response.createCollector(cli);
  for (const item of turn.setupInformation || []) collector.addInformation(item);
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const entry = store.cliEntry(sess, cli);
  // その CLI がまだ見ていない分だけ再送する（セッション ID が無い CLI は毎回ぜんぶ）
  const unseen = entry && entry.id ? history.slice(entry.seen) : history;
  const cmd = agentCli.turnCmd(spec, {
    prompt, model, readonly, cliSession: entry ? entry.id : '', history: unseen, files: attFiles,
  });
  store.appendMessage(ud, id, { role: 'user', text, cli, family, model, readonly, autoApprove, policy, tier, attachments: atts, skillSelection: selectedSkills });
  if (cmd.mintedSession) store.setCliEntry(ud, id, cli, { id: cmd.mintedSession });

  const startedAt = Date.now();
  const spec2 = spawnSpec(cmd.command, cmd.args, { cwd: dirs.fsDir, env: cmd.env, distro: distroFor(repo) });
  let child;
  try {
    child = spawn(spec2.command, spec2.args, { windowsHide: true, ...spec2.extra });
  } catch (err) {
    turn.release();
    throw new Error(`起動できません: ${(err && err.message) || err}`);
  }
  running.set(id, child);
  send('turn:started', { id, argv: cmd.argv, warning: [turn.setupWarning, cmd.readonlyWarning].filter(Boolean).join('\n') });
  send('turn:progress', { id, item: { text: `${cli} を起動しました`, status: 'running' } });
  for (const item of turn.setupInformation || []) send('turn:info', { id, item });

  let stdout = '';
  let stderr = '';
  let stopped = false;
  child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
  child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
  child.stdout.on('data', lineEmitter((line) => {
    send('turn:line', { id, kind: 'stdout', text: line });
    emitResponseParts(send, id, collector.push(line));
  }));
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
    const structured = response.parseTranscript(cli, answer);
    answer = structured.text;
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
    collector.addInformation({
      type: 'status', title: stopped ? `${cli} を停止` : `${cli} の実行`,
      status: failed ? 'error' : 'success',
      detail: `${Math.round((Date.now() - startedAt) / 1000)} 秒${code != null ? ` · 終了コード ${code}` : ''}`,
    });
    const parts = collector.parts();
    parts.thinking.push(...structured.thinking);
    const message = {
      role: 'assistant', cli, family, model, policy, tier,
      text: answer || (stopped ? '（停止した）' : `（応答なし。終了コード ${code}）`),
      code, elapsedMs: Date.now() - startedAt, stopped,
      parts,
      // 失敗の理由は定義の errors が分類できればその hint、できなければ末尾数行（認証切れのように stdout へ出す CLI もある）
      error: !failed ? '' : (rule ? rule.hint : (stderr.trim() || stdout.trim()).split('\n').slice(-6).join('\n')),
    };
    try {
      const saved = store.appendMessage(ud, id, message);
      // セッション ID が分かる CLI は、ここまでのやり取りをその CLI が見たものとして覚える
      if (sid) store.setCliEntry(ud, id, cli, { id: sid, seen: saved.messages.length });
    } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    turn.release();
    send('turn:done', { id, message });
  });
  return { pid: child.pid, argv: cmd.argv };
}

// ---- 共有（LAN の参加者として CLI を 1 回起こす） --------------------------------------------
//
// 会話を持たない単発。セッション ID も履歴も無く、定義に no_session_args があれば付けて
// 参加者の CLI にセッションを残さない。読み取り専用で起こす。
//   { cli, prompt, model, readonly, cwd, files, timeoutMs, onLine }
//   → { done: Promise<{ text, code, stopped, error, errorClass, quotaKind, elapsedMs, usage }>, stop(reason) }
function runPrompt({ cli, prompt, model = '', readonly = true, cwd, files = [], timeoutMs = 0, onLine = () => {} }) {
  const cfg = store.loadConfig(userData());
  const distro = process.platform === 'win32' ? cfg.wslDistro : '';
  const spec = agentCli.load(cli, '');
  const cmd = agentCli.turnCmd(spec, { prompt, model, readonly, cliSession: '', history: [], files });
  let argv = cmd.argv;
  if (spec.noSessionArgs && spec.noSessionArgs.length) argv = agentCli.insertAfterSubcommand(argv, spec.noSessionArgs);
  const startedAt = Date.now();
  const spec2 = spawnSpec(argv[0], argv.slice(1), { cwd: host.toHostPath(cwd), env: cmd.env, distro });
  let child;
  let stopped = false;
  let stopReason = '';
  const done = new Promise((resolve) => {
    try {
      child = spawn(spec2.command, spec2.args, { windowsHide: true, ...spec2.extra });
    } catch (err) {
      resolve({ text: '', code: 1, stopped: false, error: `起動できません: ${(err && err.message) || err}`, errorClass: 'env', quotaKind: '', elapsedMs: 0, usage: null });
      return;
    }
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
    child.stdout.on('data', lineEmitter((line) => onLine('stdout', line)));
    child.stderr.on('data', lineEmitter((line) => onLine('stderr', line)));
    child.on('error', (err) => { stderr += `\n起動エラー: ${(err && err.message) || err}`; });
    child.stdin.on('error', () => { /* 先に終わった CLI へ書いた EPIPE */ });
    child.stdin.end(cmd.stdin == null ? '' : cmd.stdin);
    const timer = timeoutMs > 0 ? setTimeout(() => { stopped = true; stopReason = '時間切れ'; killTree(child); }, timeoutMs) : null;
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      let answer = '';
      if (cmd.outputFile) {
        try { answer = fs.readFileSync(cmd.outputFile, 'utf8'); } catch { /* 書かれなかった */ }
        try { fs.unlinkSync(cmd.outputFile); } catch { /* 無ければよい */ }
      } else {
        answer = stdout;
      }
      answer = response.parseTranscript(cli, cleanAnswer(answer)).text;
      const failed = stopped || code !== 0 || !answer;
      const rule = failed ? agentCli.classifyError(spec, `${stripAnsi(stderr)}\n${stdout}`) : null;
      resolve({
        text: answer, code, stopped, elapsedMs: Date.now() - startedAt, usage: null,
        error: !failed ? '' : (stopped ? stopReason : (rule ? rule.hint : (stripAnsi(stderr).trim() || stdout.trim()).split('\n').slice(-6).join('\n'))),
        errorClass: !failed ? '' : (stopped ? 'transient' : (rule && rule.cls ? rule.cls : 'cli')),
        quotaKind: rule && rule.quotaKind ? rule.quotaKind : '',
      });
    });
  });
  return { done, stop(reason) { stopped = true; stopReason = reason || '止めた'; if (child) killTree(child); } };
}

// ---- 共有（引き受けた依頼を tmux の画面で走らせる） ------------------------------------------
//
// 依頼者は自分の端末ミラーで「他人の PC で何が起きているか」を見ながら待つ。そのため引き受けた
// 側は会話と同じ tmux セッション（agent-app-share-<依頼 id>）で CLI を起こし、画面が変わるたびに
// onScreen で渡す（participant が心拍に載せて依頼者へ送る）。tmux が無い PC ではヘッドレスに倒す。
const sharedConversations = new Map();
let shareTmuxOk = false;

function shareOutcome(message, { cli, spec, conv, startedAt, stopped = false }) {
  const structured = response.parseTranscript(cli, message.text || '');
  const halted = stopped || !!message.stopped;
  const failed = halted || !!message.error || !structured.text;
  const rule = failed ? agentCli.classifyError(spec, conv.lastText || '') : null;
  return {
    text: structured.text, code: failed ? 1 : 0, stopped: halted, elapsedMs: Date.now() - startedAt, usage: null,
    error: failed ? (message.error || '画面から答えを読み取れませんでした') : '',
    errorClass: !failed ? '' : (halted ? 'transient' : (rule && rule.cls ? rule.cls : 'cli')),
    quotaKind: rule && rule.quotaKind ? rule.quotaKind : '',
  };
}

function runPromptTmux(opts) {
  const { cli, prompt, model = '', cwd, timeoutMs = 0, onScreen = () => {}, shareId } = opts;
  const cfg = store.loadConfig(userData());
  const spec = agentCli.load(cli, '');
  const { shell } = host.hostOf('', cfg.wslDistro);
  const cmd = agentCli.interactiveCmd(spec, { model, readonly: true, autoApprove: false, cliSession: '', history: [] });
  const id = `share-${shareId}`;
  const startedAt = Date.now();
  let stopped = false;
  const conv = new tmux.Conversation({
    id, shell, cwd: host.toHostPath(cwd), argv: cmd.argv, patterns: tmux.compilePatterns(spec.interactive),
    launch: { cli, model, readonly: true, autoApprove: false },
    emit: (channel, payload) => { if (channel === 'term:screen') onScreen(payload.text); },
  });
  conv.watchers = 1;                       // 依頼者が見ているので、画面は常に取る
  sharedConversations.set(id, conv);
  let timer = null;
  const cleanup = async () => {
    if (timer) clearTimeout(timer);
    sharedConversations.delete(id);
    await conv.kill().catch(() => {});
  };
  const done = new Promise((resolve) => {
    (async () => {
      try {
        await conv.open({ reuse: false });
        await conv.waitReady();
        if (timeoutMs > 0) timer = setTimeout(() => { stopped = true; conv.stop().catch(() => {}); }, timeoutMs);
        await conv.send(prompt, (message) => resolve(shareOutcome(message, { cli, spec, conv, startedAt, stopped })));
      } catch (err) {
        resolve({ text: '', code: 1, stopped: false, error: `起動できません: ${(err && err.message) || err}`, errorClass: 'env', quotaKind: '', elapsedMs: Date.now() - startedAt, usage: null });
      }
    })();
  }).then(async (outcome) => { await cleanup(); return outcome; });
  return {
    done,
    // 止めるときは生成を止めてから tmux ごと終わらせる（kill が待っているターンも閉じる）
    stop() {
      stopped = true;
      conv.stop().catch(() => {}).then(() => cleanup()).catch(() => {});
    },
  };
}

// 共有で 1 件を走らせる。tmux が使えて対話定義のある CLI なら画面つき、無ければヘッドレス。
function runSharedPrompt(opts) {
  const spec = opts.cli ? agentCli.load(opts.cli, '') : null;
  const cfg = store.loadConfig(userData());
  if (shareTmuxOk && opts.shareId && spec && spec.interactive && cfg.transport !== 'headless') {
    try { return runPromptTmux(opts); } catch { /* 定義や tmux の都合で作れなければヘッドレス */ }
  }
  return runPrompt(opts);
}

// 登録リポジトリの origin URL（共有の依頼の workspace と突き合わせる）。60 秒ごとに引き直す。
const repoUrls = new Map();
function normalizeRepoUrl(url) {
  return String(url || '').trim().toLowerCase()
    .replace(/\/+$/, '')            // 末尾の /
    .replace(/\.git$/, '')          // .git
    .replace(/^[a-z+]+:\/\//, '')   // scheme://
    .replace(/^[^@/:]+@/, '')       // user@
    .replace(/^([^/:]+):/, '$1/');  // host:path → host/path
}
async function refreshRepoUrls() {
  const cfg = store.loadConfig(userData());
  const next = new Map();
  for (const repo of cfg.repos) {
    try {
      const dirs = dirsOf(repo, '');
      const r = await host.shellFor(distroFor(repo)).exec(['git', '-C', dirs.hostDir, 'remote', 'get-url', 'origin'], { timeoutMs: 5000 });
      if (r.ok && r.output.trim()) next.set(normalizeRepoUrl(r.output), repo);
    } catch { /* origin が無いリポジトリは突き合わせの対象外 */ }
  }
  repoUrls.clear();
  for (const [k, v] of next) repoUrls.set(k, v);
  return repoUrls;
}
function repoFor(url) { return repoUrls.get(normalizeRepoUrl(url)) || ''; }

let shareInstance = null;

// 起動方針「共有」。本文はヘッドレスと同じ順で合成し（共通指示 → スキル本文 → 履歴の再送 → 依頼）、
// LAN の参加者へ渡す。答えは requester が同じ会話へ assistant のメッセージとして戻す（turn:done）。
// スキルは相手の PC に無い前提で、常に SKILL.md の本文を埋め込む。
async function runShared(id, sess, dirs, p, requested, cfg, send, release) {
  if (!shareInstance || shareInstance.state !== 'on') {
    throw new Error(shareInstance && shareInstance.error ? shareInstance.error : '共有が動いていません（設定 > 共有）');
  }
  const ud = userData();
  const repo = sess.repo;
  const atts = [];
  const served = [];
  const lines = [];
  for (const a of (Array.isArray(p.attachments) ? p.attachments : []).slice(0, attachments.MAX_PER_TURN)) {
    if (!a || typeof a !== 'object') continue;
    if (a.id) {
      const { path: file, size } = attachments.resolve(ud, a.id, a.name);
      const name = attachments.safeName(a.name);
      atts.push({ id: attachments.checkId(a.id), name, size });
      served.push({ name, path: file });
    } else if (a.rel) {
      const { rel } = files.resolveInside(dirs.fsDir, String(a.rel));
      atts.push({ rel, name: rel.split('/').pop() });
      lines.push(`- ${rel}（作業フォルダの中）`);
    }
  }
  let text = requested.text;
  if (lines.length) text = `${text}\n\n添付ファイル（必要に応じて読んで参照すること）:\n${lines.join('\n')}`;
  const selectionConfig = cfg.instructions.skillSelection || {};
  const selectedSkills = skillSelection.select({
    mode: p.skillMode || selectionConfig.defaultMode || 'auto',
    text: [requested.text, ...atts.map((item) => item.name || item.rel || '')].join('\n'),
    requested: p.skills,
    candidates: selectionConfig.enabled === false ? [] : selectionConfig.candidates,
    catalog: skills.catalog(repo),
  });
  const skillDelivery = skillSelection.deliver(selectedSkills, { slashNative: false });
  const instructed = sessionSetup.withInstructions(text, cfg.instructions);
  const contextual = skillDelivery.instruction ? `${skillDelivery.instruction}\n\n${instructed}` : instructed;
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const goal = history.length ? agentCli.replayPrompt(history, contextual) : contextual;
  let workspace = null;
  try {
    const r = await host.shellFor(distroFor(repo)).exec(['git', '-C', dirs.hostDir, 'remote', 'get-url', 'origin'], { timeoutMs: 5000 });
    if (r.ok && r.output.trim()) workspace = { url: r.output.trim(), base: sess.branch || '' };
  } catch { /* origin が無ければリポジトリ無しの依頼 */ }
  store.appendMessage(ud, id, {
    role: 'user', text: requested.text, cli: requested.cli || '', model: requested.model, readonly: true, autoApprove: false,
    policy: settings.SHARED_POLICY, tier: '', attachments: atts, skillSelection: selectedSkills,
  });
  if (conversations.has(id) || sess.live) await closeConversation(id);
  const request = shareInstance.post({
    sessionId: id, title: requested.text.split('\n')[0], summary: requested.text, goal, requires: { agent_cli: requested.cli ? [requested.cli] : [] },
    mode: 'read', model: requested.model, priority: p.priority || 'normal', attachments: served, workspace,
  }, { onDone: release });
  store.updateSession(ud, id, {
    cli: requested.cli || sess.cli, model: requested.model, readonly: true, policy: settings.SHARED_POLICY, tier: '', transport: 'headless',
    share: { id: request.id },
  });
  send('turn:started', { id, argv: [], warning: '' });
  send('turn:progress', { id, item: { text: `共有の列に並べた（${request.id}）`, status: 'running' } });
  for (const item of skillDelivery.information) send('turn:info', { id, item });
  return { pid: 0, argv: [], shared: request.id };
}

// ---- tmux（対話起動）。会話 ID → Conversation ----------------------------------------

const conversations = new Map();

function sameLaunch(a, b) {
  return !!a && !!b && a.cli === b.cli && String(a.model || '') === String(b.model || '')
    && Boolean(a.readonly) === Boolean(b.readonly) && Boolean(a.autoApprove) === Boolean(b.autoApprove);
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
  const defaults = { cli: sess.cli, model: sess.model || '', readonly: !!sess.readonly, autoApprove: !!sess.autoApprove };
  const existing = conversations.get(id);
  const live = existing ? existing.launch : sess.live;
  let want = launch || (fresh ? defaults : (live || defaults));
  // 会話の既定が `herd` なら（会話を開いただけ・再起動）、一族の共通 TUI を開く
  if (herd.isHerd(want.cli)) want = { ...want, cli: concreteCli({ ...want }, await listAgents(repo)).cli };
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
  const cmd = agentCli.interactiveCmd(spec, {
    model: want.model, readonly: want.readonly, autoApprove: want.autoApprove,
    cliSession: entry ? entry.id : '', history,
  });
  const conv = new tmux.Conversation({
    id, shell, cwd, argv: cmd.argv, patterns: tmux.compilePatterns(spec.interactive), cols, rows, launch: want,
    emit: (channel, payload) => {
      if (channel === 'term:snapshot') {
        store.addTerminalSnapshot(ud, id, {
          agentCli: want.cli, model: want.model, reason: payload.reason, screenText: payload.screenText,
        });
        return;
      }
      send(channel, payload);
    },
  });
  if (restart && live) {
    const source = existing || conv;
    const captured = await source.capture({ history: true }).catch(() => ({ ok: false }));
    if (captured.ok && stripAnsi(captured.screen.text).trim()) {
      store.addTerminalSnapshot(ud, id, {
        agentCli: live.cli, model: live.model, reason: 'agent_switch', screenText: stripAnsi(captured.screen.text),
      });
    }
  }
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
  store.touchTerminalSession(ud, id, {
    name: conv.name, state: 'active', ownerInstanceId: instanceId, cli: want.cli, model: want.model,
  });
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
  store.clearTerminalSession(userData(), id);
}

async function sweepTerminalSessions() {
  const ud = userData();
  for (const sess of store.staleTerminalSessions(ud)) {
    if (conversations.has(sess.id) || running.has(sess.id)) continue;
    const terminal = sess.terminalSession;
    const expected = tmux.sessionName(sess.id);
    if (!terminal || terminal.name !== expected) continue;
    try {
      const { shell: targetShell } = host.hostOf(sess.repo, store.loadConfig(ud).wslDistro);
      await targetShell.run(tmux.cmdKill(expected));
      store.clearTerminalSession(ud, sess.id);
      store.updateSession(ud, sess.id, { live: null });
    } catch { /* 次回の起動・定期清掃で再試行する */ }
  }
}

async function runTmux(id, turn, send) {
  const ud = userData();
  const { cli, model, readonly, autoApprove, text, prompt, atts, policy, tier, selectedSkills, family = '', slash = '' } = turn;
  const want = { cli, model, readonly, autoApprove };
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
  // セッション開始スキルは本依頼へ連結しない。1 件ずつ独立した入力として適用し、
  // 完了を待ってからユーザーの依頼を送る（agent-loop の chat strategy=paste と同じ境界）。
  for (const item of turn.setupSkills || []) {
    const result = await new Promise((resolve, reject) => {
      const enterCount = cli === 'codex' && item.command.trim().startsWith('$') ? 2 : 1;
      conv.send(item.command, resolve, { enterCount }).catch(reject);
    });
    const ok = !result.error;
    turn.setupInformation.push({
      type: 'command', title: item.command, status: ok ? 'success' : 'error',
      detail: ok ? 'セッションへ適用しました' : result.error,
    });
    if (!ok) {
      const message = `開始スキルの適用に失敗しました: ${item.command}`;
      if (item.onError === 'fail') throw new Error(message);
      turn.setupWarning = [turn.setupWarning, message].filter(Boolean).join('\n');
    }
  }
  const sess = store.readSession(ud, id);
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  const unseen = history.slice(conv.seen);
  // 用途のスラッシュ行は（履歴の再送があっても）本文の一番上。共通 TUI は先頭の /name 行だけを読む
  const full = herd.withSlash(slash, unseen.length ? agentCli.replayPrompt(unseen, prompt, { resumed: conv.resumed }) : prompt);
  store.appendMessage(ud, id, { role: 'user', text, cli, family, model, readonly, autoApprove, policy, tier, attachments: atts, skillSelection: selectedSkills });
  await conv.send(full, (message) => {
    const structured = response.parseTranscript(cli, message.text);
    message.text = structured.text;
    message.cli = cli;
    message.family = family;
    message.model = model;
    message.policy = policy;
    message.tier = tier;
    message.parts = {
      thinking: structured.thinking,
      information: [
        ...(turn.setupInformation || []),
        { type: 'status', title: `${cli} の対話セッション`, status: message.error ? 'error' : 'success', detail: '' },
      ],
    };
    try {
      const saved = store.appendMessage(ud, id, message);
      conv.seen = saved.messages.length;
      store.setCliEntry(ud, id, cli, { seen: saved.messages.length });
    } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
    turn.release();
    send('turn:done', { id, message });
  });
  const warning = [turn.setupWarning, opened ? opened.warning : ''].filter(Boolean).join('\n');
  send('turn:started', { id, argv: [], warning });
  send('turn:progress', { id, item: { text: `${cli} が依頼を処理しています`, status: 'running' } });
  for (const item of turn.setupInformation || []) send('turn:info', { id, item });
  return { name: conv.name, restarted: !!(opened && opened.restarted), warning };
}

// 1 ターン。CLI・モデル・モードはターンごとに決め、tmux か ヘッドレスかもここで決める
// （対話定義を持つ CLI で tmux が使えるなら tmux）。
async function runTurn(id, p, send, { config = null, release = () => {} } = {}) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const dirs = dirsOf(sess.repo, sess.worktree || '', { mustExist: true });
  const cfg = config || store.loadConfig(ud);
  const agents = await listAgents(repo);
  const requested = executionSpec(sess, p, cfg, { optimized: settings.optimized(cfg, { herdAvailable: agentsMod.herdAvailable(agents) }) });
  if (requested.policy === settings.SHARED_POLICY) return runShared(id, sess, dirs, p, requested, cfg, send, release);
  const base = concreteCli(requested, agents, { attachments: p.attachments });
  const spec = agentCli.load(base.cli, repo);
  const available = agents.find((item) => item.name === base.cli);
  if (!available || !available.available) {
    const error = new Error(`${base.cli} はこの実行環境で利用できません。設定の tier または直接指定を確認してください`);
    error.code = 'AGENT_UNAVAILABLE';
    throw error;
  }
  const familyInfo = base.family
    ? [{ type: 'status', title: `${base.family} → ${base.cli}${base.slash ? ` ${base.slash}` : ''}`, status: 'success', detail: base.familyReason }] : [];
  let transport = 'headless';
  if (cfg.transport === 'tmux' && spec.interactive) {
    const info = await host.probe(distroFor(repo));
    if (info.ok && info.tmux) transport = 'tmux';
  }
  const attached = withAttachments(ud, base.text, p.attachments, dirs);
  let setupInformation = [...familyInfo];
  let setupWarning = '';
  let setupSkills = [];
  // CLI ごとの最初の起動だけに開始アクションを適用する。既存 entry は設定変更後も再実行しない。
  if (!store.cliEntry(sess, base.cli)) {
    const plan = sessionSetup.planActions(cfg.instructions.startupActions, {
      ...spec,
      availableSkills: skills.list(repo),
    });
    const startup = await sessionSetup.runCommands(plan.commands, (command, timeoutMs) => {
      const script = `cd ${host.sq(dirs.hostDir)} && ${command}`;
      return host.shellFor(distroFor(repo)).run(script, { timeoutMs });
    });
    setupInformation = startup.information;
    setupWarning = [plan.warning, startup.warning].filter(Boolean).join('\n');
    setupSkills = plan.skills;
    store.setCliEntry(ud, id, base.cli, { setupApplied: true });
  }
  const selectionConfig = cfg.instructions.skillSelection || {};
  const selectedSkills = skillSelection.select({
    mode: p.skillMode || selectionConfig.defaultMode || 'auto',
    text: [base.text, ...(Array.isArray(p.attachments) ? p.attachments.map((item) => item.name || item.rel || '') : [])].join('\n'),
    requested: p.skills,
    candidates: selectionConfig.enabled === false ? [] : selectionConfig.candidates,
    catalog: skills.catalog(repo),
  });
  const skillDelivery = skillSelection.deliver(selectedSkills, spec);
  setupInformation.push(...skillDelivery.information);
  setupSkills.push(...skillDelivery.commands.map((command) => ({
    command, name: command.replace(/^[$/]+/, ''), onError: selectedSkills.mode === 'manual' ? 'fail' : 'warn',
  })));
  // ヘッドレスには対話セッションが無いため、先頭のコマンドブロックとして同じ実行へ載せる。
  // tmux は runTmux が 1 件ずつ先に送るので、本依頼へ混ぜない。
  // 「会話」だけ、別のリポジトリへ分岐する作法（@fork 行）を添える。タスクを AI と作る会話には添えない
  const instructedPrompt = sessionSetup.withInstructions(attached.prompt, cfg.instructions, {
    fork: sess.kind === 'conversation' ? { repos: cfg.repos, current: sess.repo } : null,
  });
  const contextualPrompt = skillDelivery.instruction ? `${skillDelivery.instruction}\n\n${instructedPrompt}` : instructedPrompt;
  const prompt = transport === 'headless' && setupSkills.length
    ? `${setupSkills.map((item) => item.command).join('\n')}\n\n${contextualPrompt}`
    : contextualPrompt;
  const turn = { ...base, prompt, atts: attached.atts, files: attached.files, spec, setupInformation, setupWarning, setupSkills, selectedSkills, release };
  // 次のターンの既定として覚える（画面はこれを出す）。`herd` は写した先ではなく要求した
  // 名前のまま残す——次のターンは添付の有無でまた選び直す
  store.updateSession(ud, id, {
    cli: base.requested || base.cli, model: base.model, readonly: base.readonly, autoApprove: base.autoApprove,
    policy: base.policy, tier: base.tier, transport,
  });
  if (transport === 'headless') {
    // ヘッドレスの CLI へ移るなら、動いていた tmux の CLI は止める（同時に 2 つは持たない）
    if (conversations.has(id) || sess.live) await closeConversation(id);
    return runHeadless(id, turn, send);
  }
  return runTmux(id, turn, send);
}

async function guardedRunTurn(id, p, send) {
  const cfg = store.loadConfig(userData());
  turnGate.acquire(id, cfg.execution.maxConcurrent);
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    turnGate.release(id, cfg.execution.maxConcurrent);
  };
  try {
    return await runTurn(id, p, send, { config: cfg, release });
  } catch (error) {
    release();
    throw error;
  }
}

// ---- 登録 ------------------------------------------------------------------------

// 定義の一覧と「使える」印（agents.js。タスク・ワークフローも同じ一覧を見る）。
function listAgents(repo) {
  const distro = repo ? distroFor(repo) : host.hostOf('', store.loadConfig(userData()).wslDistro).distro;
  return agentsMod.listAgents(repo, { distro });
}

// 名前検索の索引の材料。Windows で \\wsl$\ のリポジトリ（実体は WSL の中）を読むときだけ、
// ホスト（WSL）の中で `git ls-files` を 1 回撃つ。Windows 側の fs から 9P 越しに歩くと
// readdir 1 回ごとに往復が要り、索引作りが何秒もかかる。返るのは相対パスなので表記の変換は
// 要らず、.gitignore も効く。git リポジトリでなければ null（→ fs で歩く）。
// C:\ のリポジトリと Linux / macOS は fs で歩くほうが速いので lister を返さない。
function hostLister(repo, dirs) {
  if (process.platform !== 'win32' || !host.isWslUnc(dirs.fsDir)) return null;
  return async () => {
    const r = await host.shellFor(distroFor(repo)).exec(
      ['git', '-C', dirs.hostDir, 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], { timeoutMs: 60000 },
    );
    return r.ok ? r.output.split('\0').filter(Boolean) : null;
  };
}

// statemachine-maker はここから `../../.github/skills/statemachine-use` を辿ってスキルの
// スクリプトを探す（登録リポジトリや設定で見つからないときの最後の候補）。開発起動なら
// このリポジトリのソースツリー、パッケージ版なら extraResources で resources/app-root/ に
// 同梱した写し（リポジトリ直下と同じ相対配置）を指す。
function automationAppRoot() {
  const packaged = process.resourcesPath ? path.join(process.resourcesPath, 'app-root') : '';
  if (packaged && fs.existsSync(path.join(packaged, '.github', 'skills', 'statemachine-use'))) {
    return path.join(packaged, 'tools', 'agent-app');
  }
  return path.join(__dirname, '..', '..');
}

// ---- タスクを AI と作る会話（tmux）。会話基盤をそのまま使い、kind: 'task' の会話をタスクに紐づける ----
//
// 手動実行の画面と同じく、作成・変更も tmux の端末ミラーの中で進める。CLI は会話と同じ
// 定義・同じ起動方針で起こし、cwd はリポジトリ本体。最初の依頼（teaching.prompt）が
// statemachine-use の作成モードと、見本の依頼の作法（@record 行）を伝える。
// ブラウザの見本は、**この端末**（Windows ならその Windows 側）で Edge をリモートデバッグ付きで起こし、
// 固定文で AI に知らせて AI 自身が CDP 越しに記録を取る（automation:teach:browser。固定文は renderer が
// 会話の送信経路で送る）。ボタンは 1 つで、押すたびに「開く（準備）→ 記録開始 → 終了」と進み、段ごとに
// 別の固定文（@recording open / start / stop）が渡る。Windows アプリの見本（winauto）はこの端末で取り、できた Markdown の所在を
// WSL 表記に直して会話へ送る。

function teachingTools() {
  return {
    browser: !!recordingBrowser.findBrowser({ resolvePath: (name) => agentCli.resolvePath(name) }),
    windows: process.platform === 'win32' && !!agentCli.resolvePath('winauto'),
  };
}

// 「ブラウザを開く」: Edge（無ければ Chrome）を記録専用プロファイルで、リモートデバッグ付きで起こす。
// この時点ではまだ記録は始まらない（利用者がログインや画面の移動をする）。
function launchTeachingBrowser(p) {
  return recordingBrowser.launchRecordingBrowser({
    url: p.url, profileDir: path.join(userData(), recordingBrowser.PROFILE_DIR),
    resolvePath: (name) => agentCli.resolvePath(name),
  });
}

// 「記録を始める」: 準備の間に利用者が移動した先を記録の起点として AI へ渡すため、いま開いている
// ページを DevTools から読む。読めなくても記録は始められるので、失敗は url: '' で返す。
function teachingBrowserPage() {
  return recordingBrowser.activePage();
}

function teachingSkillDir(repo, cfg) {
  const dir = automationTools.findSkillDir({ root: repo, configured: cfg.automationSkillDir, appRoot: automationAppRoot() });
  return dir ? host.toHostPath(dir) : '';
}

function taskConversationView(ud, repo, machine) {
  const summary = store.findTaskSession(ud, repo, machine);
  const session = summary ? presentSession(store.readSession(ud, summary.id)) : null;
  return {
    machine, session, sidecar: teaching.load(repo, machine), published: machineStore.exists(repo, machine),
    tools: teachingTools(),
  };
}

function prepareTeaching(p) {
  const ud = userData();
  const repo = requireRepo(p.repo);
  const cfg = store.loadConfig(ud);
  const purpose = String(p.purpose || '').trim();
  const machine = String(p.machine || '').trim() || teaching.machineNameFor(purpose);
  machineStore.machineDir(repo, machine);            // 保存名の字種を検査する（不正なら投げる）
  const existing = machineStore.exists(repo, machine);
  let sidecar = teaching.load(repo, machine);
  if (!existing && !sidecar) {
    if (!purpose) throw new Error('教えたいタスクを入力してください');
    sidecar = teaching.save(repo, machine, { title: purpose.split(/\r?\n/)[0].slice(0, 80), purpose });
  }
  let summary = store.findTaskSession(ud, repo, machine);
  if (!summary) {
    const selected = settings.resolve(cfg, p.policy ? p : { policy: 'direct', cli: p.cli || cfg.execution.tiers.medium.cli, model: p.model });
    const created = store.createSession(ud, {
      repo, cli: selected.cli, model: selected.model, policy: selected.policy, tier: selected.tier,
      readonly: false, autoApprove: p.autoApprove != null ? !!p.autoApprove : cfg.execution.defaultAutoApprove,
      transport: 'tmux', worktree: '', kind: 'task', task: { machine },
    });
    summary = { id: created.id };
    sidecar = teaching.save(repo, machine, { ...(sidecar || { title: machine, purpose }), sessionId: created.id });
  } else if (sidecar && sidecar.sessionId !== summary.id) {
    sidecar = teaching.save(repo, machine, { ...sidecar, sessionId: summary.id });
  }
  // 既にある会話でも権限は画面の選択に合わせる（自動承認へ切り替えたら、次の依頼で CLI を起動し直す）
  if (p.autoApprove != null) store.updateSession(ud, summary.id, { autoApprove: !!p.autoApprove });
  const session = store.readSession(ud, summary.id);
  return { ud, repo, cfg, purpose, machine, existing, sidecar, session };
}

function prepareTeachingView(p) {
  const prepared = prepareTeaching(p);
  return { ...taskConversationView(prepared.ud, prepared.repo, prepared.machine), existing: prepared.existing };
}

async function startTeaching(p, send) {
  const { ud, repo, cfg, purpose, machine, existing, sidecar, session } = prepareTeaching(p);
  const conversation = conversations.get(session.id);
  const busy = running.has(session.id) || !!(conversation && conversation.turn);
  let started = false;
  // 初回だけでなく、下書きの再開・公開済みタスクの編集開始時にも対象を明示する。
  // renderer は起動待ちを見せるため先に tmux へ接続するので、生きていること自体を
  // 「依頼済み」の印にはしない。編集開始という明示操作ごとに対象を伝える。
  if (!busy) {
    const common = { machine, purpose: sidecar ? sidecar.purpose : purpose, existing };
    const prompt = session.messages.length
      ? teaching.resumePrompt({ ...common, context: p.context })
      : teaching.prompt({ ...common, skillDir: teachingSkillDir(repo, cfg), tools: teachingTools() });
    await guardedRunTurn(session.id, {
      prompt, policy: session.policy, cli: session.cli, model: session.model, readonly: false, autoApprove: session.autoApprove,
      skillMode: 'off', skills: [], attachments: [],
    }, send);
    started = true;
  }
  return { ...taskConversationView(ud, repo, machine), existing, started };
}

// Windows アプリの見本を保存し、AI へ渡す本文を**返す**。送りはしない——本文は入力欄に入り、
// 利用者が見たものの補足を足してから送る（ブラウザの「終了してAIへ渡す」と同じ扱い）。
// 送る経路が会話の 1 本だけになるので、AI が応答中でもここで断る必要がない。
function demonstrate(p) {
  const repo = requireRepo(p.repo);
  const machine = String(p.machine || '').trim();
  const saved = teaching.saveRecording(repo, machine, p.recording);
  const hostPath = host.toHostPath(saved.file);
  const prompt = teaching.demonstrationPrompt({
    machine, hostPath, source: saved.source, target: saved.target, steps: saved.steps,
    parameters: saved.parameters, requested: p.requested !== false,
  });
  return { file: saved.file, relative: saved.relative, hostPath, source: saved.source, steps: saved.steps, prompt };
}

function registerIpcHandlers(getWindow) {
  const send = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };
  registerAutomationIpc({
    getWindow,
    userData,
    appRoot: automationAppRoot(),
  });
  handle('automation:teach:prepare', (p) => prepareTeachingView(p));
  handle('automation:teach:start', (p) => startTeaching(p, send));
  handle('automation:teach:session', (p) => taskConversationView(userData(), requireRepo(p.repo), String(p.machine || '').trim()));
  handle('automation:teach:demonstration', (p) => demonstrate(p));
  handle('automation:teach:browser', (p) => launchTeachingBrowser(p));
  handle('automation:teach:browser:page', () => teachingBrowserPage());
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
    if (shareInstance) shareInstance.reconfigure(next).catch(() => {});
    if (JSON.stringify(before.repos) !== JSON.stringify(next.repos)) refreshRepoUrls().catch(() => {});
    return next;
  });

  // 共有（LAN の参加者に依頼を回す）。投函は turn:send の policy: 'shared'。ここは観測と調整だけ
  let shareAgentNames = [];
  const refreshShareCaches = async () => {
    try { shareAgentNames = (await listAgents('')).filter((a) => a.available && !a.virtual).map((a) => a.name); } catch { /* 次の周で */ }
    try { const info = await host.probe(process.platform === 'win32' ? store.loadConfig(userData()).wslDistro : ''); shareTmuxOk = !!(info.ok && info.tmux); } catch { shareTmuxOk = false; }
    await refreshRepoUrls().catch(() => {});
  };
  shareInstance = new share.Share({ userData: userData(), config: store.loadConfig(userData()), send, runPrompt: runSharedPrompt, agents: () => shareAgentNames, repoFor });
  refreshShareCaches().then(() => shareInstance.start()).catch((err) => { shareInstance.error = err.message; });
  const shareTimer = setInterval(() => { refreshShareCaches().catch(() => {}); }, 5 * 60 * 1000);
  if (shareTimer.unref) shareTimer.unref();
  handle('share:status', () => shareInstance.status());
  handle('share:cancel', (p) => shareInstance.cancel(String(p.id || '')));
  handle('share:priority', (p) => shareInstance.setPriority(String(p.id || ''), p.priority));
  handle('share:accept', (p) => shareInstance.accept(String(p.id || '')));
  handle('share:stop', (p) => shareInstance.stopAccepted(String(p.id || '')));
  handle('share:screen', (p) => shareInstance.screenOf(String(p.id || '')));
  // 引き受け方（自動で受ける / 選んで受ける / 受けない）。設定 > 共有と同じ値を書き換える
  handle('share:mode', async (p) => {
    const current = store.loadConfig(userData());
    const next = store.saveConfig(userData(), { share: { ...current.share, accept: String(p.mode || 'off') } });
    await shareInstance.reconfigure(next);
    return shareInstance.status();
  });
  handle('share:participate', async (p) => {
    const current = store.loadConfig(userData());
    const next = store.saveConfig(userData(), { share: { ...current.share, accept: p.on ? 'auto' : 'off' } });
    await shareInstance.reconfigure(next);
    return shareInstance.status();
  });

  handle('repo:add', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: 'リポジトリを登録する' });
    if (res.canceled || !res.filePaths.length) return null;
    return store.addRepo(userData(), res.filePaths[0]);
  });
  handle('repo:remove', (p) => store.removeRepo(userData(), p.repo));
  handle('agents:list', (p) => listAgents(p.repo ? requireRepo(p.repo) : ''));
  handle('skills:list', (p) => skills.list(p.repo ? requireRepo(p.repo) : ''));
  handle('skills:select', (p) => {
    const repo = p.repo ? requireRepo(p.repo) : '';
    const cfg = store.loadConfig(userData());
    const selectionConfig = cfg.instructions.skillSelection || {};
    const result = skillSelection.select({
      mode: p.mode || selectionConfig.defaultMode || 'auto', text: p.text,
      requested: p.selected,
      candidates: selectionConfig.enabled === false ? [] : selectionConfig.candidates,
      catalog: skills.catalog(repo),
    });
    return { ...result, selected: result.selected.map(({ content, path: skillPath, ...item }) => item) };
  });

  handle('session:list', (p) => store.listSessions(userData(), p.repo || ''));
  handle('session:create', async (p) => {
    const repo = requireRepo(p.repo);
    const cfg = store.loadConfig(userData());
    const selected = settings.resolve(cfg, p.policy ? p : {
      policy: 'direct', cli: p.cli || cfg.execution.tiers.medium.cli, model: p.model,
    });
    let branch = '';
    if (p.worktree) branch = (await worktree.find(repo, p.worktree, distroFor(repo))).branch;
    return store.createSession(userData(), {
      ...p, repo, branch, cli: selected.cli, model: selected.model,
      policy: selected.policy, tier: selected.tier,
      readonly: p.readonly != null ? p.readonly : cfg.execution.defaultReadonly,
      autoApprove: p.autoApprove != null ? p.autoApprove : cfg.execution.defaultAutoApprove,
    });
  });
  handle('session:read', (p) => presentSession(store.readSession(userData(), p.id)));
  // 別のリポジトリへ分岐する: 元の会話の起動条件を写した新しい会話を分岐先のリポジトリ本体に作り、
  // 元の会話の所在を添えた依頼文を最初のターンとして送る。分岐先も登録済みリポジトリに限る。
  handle('session:fork', async (p) => {
    const ud = userData();
    const origin = store.readSession(ud, p.originId);
    const repo = requireRepo(p.repo);
    if (repo === origin.repo) throw new Error('分岐先には別のリポジトリを選んでください');
    const prompt = String(p.prompt || '').trim();
    if (!prompt) throw new Error('分岐先へ送る依頼が空です');
    const created = store.createSession(ud, {
      repo, cli: origin.cli, model: origin.model, policy: origin.policy, tier: origin.tier,
      readonly: origin.readonly, autoApprove: origin.autoApprove, transport: origin.transport, worktree: '',
      origin: { sessionId: origin.id, repo: origin.repo, index: Number(p.index) },
    });
    const turn = await guardedRunTurn(created.id, {
      prompt: forkProtocol.forkPrompt({ originRepo: origin.repo, originTitle: origin.title, prompt }),
      policy: created.policy, cli: created.cli, model: created.model, readonly: created.readonly, autoApprove: created.autoApprove,
      skillMode: p.skillMode || 'auto', skills: [], attachments: [],
    }, send);
    return { session: presentSession(store.readSession(ud, created.id), ud), turn };
  });
  handle('session:update', (p) => store.updateSession(userData(), p.id, p.patch));
  handle('session:remove', async (p) => {
    if (running.has(p.id)) running.get(p.id).stop();
    const conv = conversations.get(p.id);
    if (conv) { conversations.delete(p.id); await conv.kill(); }
    try { attachments.discardAll(userData(), store.readSession(userData(), p.id)); } catch { /* 会話が読めなければ添付も辿れない */ }
    return store.removeSession(userData(), p.id);
  });

  handle('turn:send', (p) => guardedRunTurn(p.id, p, send));

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
    if (shareInstance && shareInstance.pendingSessionIds().includes(p.id)) { await shareInstance.cancelSession(p.id); return true; }
    const c = running.get(p.id);
    if (c) { c.stop(); return true; }
    const conv = conversations.get(p.id);
    return conv ? conv.stop() : false;
  });
  handle('turn:running', () => [...new Set([
    ...turnGate.snapshot(store.loadConfig(userData()).execution.maxConcurrent).ids,
    ...running.keys(),
    ...[...conversations.values()].filter((c) => c.turn).map((c) => c.id),
    ...(shareInstance ? shareInstance.pendingSessionIds() : []),
  ])]);

  // 端末（tmux）
  handle('term:open', (p) => openConversation(p.id, send, { cols: p.cols, rows: p.rows }));
  handle('term:restart', (p) => openConversation(p.id, send, { cols: p.cols, rows: p.rows, fresh: true }));
  handle('term:state', (p) => {
    const conv = conversations.get(p.id);
    return conv ? { name: conv.name, phase: conv.phase, detail: conv.detail, busy: !!conv.turn } : null;
  });
  handle('term:watch', (p) => { const c = conversations.get(p.id); if (c) c.watch(); return !!c; });
  handle('term:unwatch', (p) => { const c = conversations.get(p.id); if (c) c.unwatch(); return !!c; });
  handle('term:submit', async (p) => {
    const conv = conversations.get(p.id);
    if (!conv) throw new Error('端末が開いていない');
    const text = String(p.text || '').trim();
    if (!text) throw new Error('送信する内容がありません');
    const result = await conv.submit(text);
    let warning = '';
    try {
      const sess = store.readSession(userData(), p.id);
      const live = sess.live || conv.launch || {};
      store.appendMessage(userData(), p.id, {
        role: 'user', text, cli: live.cli || sess.cli, model: live.model || sess.model,
        readonly: live.readonly == null ? !!sess.readonly : !!live.readonly,
        autoApprove: live.autoApprove == null ? !!sess.autoApprove : !!live.autoApprove,
        policy: sess.policy || 'direct', tier: sess.tier || '', attachments: [],
      });
      store.touchTerminalSession(userData(), p.id, { state: 'active', ownerInstanceId: instanceId });
    } catch (err) {
      // CLI への送信自体は完了している。失敗扱いにすると入力欄が残り、二重送信を招く。
      warning = `送信済みですが、会話履歴へ保存できませんでした: ${err.message}`;
    }
    return { ...result, followup: true, warning };
  });
  handle('term:keys', async (p) => {
    const c = conversations.get(p.id);
    if (!c) throw new Error('端末が開いていない');
    // 1 キーごとのセッション保存は入力遅延を生む。期限は open/submit/終了時に更新する。
    return c.keys(String(p.data || ''));
  });
  handle('term:scroll', (p) => {
    const c = conversations.get(p.id);
    if (!c) throw new Error('端末が開いていない');
    return c.scroll(p.lines);
  });
  handle('term:resize', (p) => { const c = conversations.get(p.id); return c ? c.resize(p.cols, p.rows) : false; });
  handle('term:kill', async (p) => { const had = conversations.has(p.id); await closeConversation(p.id); return had; });

  // 作業フォルダ（git worktree）
  // withStatus: false なら `git worktree list` だけ（画面はまず一覧を出し、変更数はあとから足す。
  // Windows の /mnt/c では status が数秒〜数十秒かかり、待つと起動時に何も選べない）
  handle('wt:list', (p) => { const repo = requireRepo(p.repo); return worktree.list(repo, distroFor(repo), { withStatus: p.withStatus !== false }); });
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
  handle('fs:find', (p) => {
    const dirs = dirsOf(p.repo, p.worktree);
    return files.find(dirs.fsDir, p.query || '', 200, { refresh: !!p.refresh, lister: hostLister(p.repo, dirs) });
  });

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

  const sweepTimer = setInterval(() => sweepTerminalSessions().catch(() => {}), 60 * 60 * 1000);
  if (sweepTimer.unref) sweepTimer.unref();
  setTimeout(() => sweepTerminalSessions().catch(() => {}), 0);

  app.on('before-quit', () => {
    clearInterval(sweepTimer);
    clearInterval(shareTimer);
    if (shareInstance) shareInstance.stop().catch(() => {});
    for (const c of running.values()) c.stop();
    for (const c of conversations.values()) {
      try { store.touchTerminalSession(userData(), c.id, { state: 'idle', ownerInstanceId: instanceId }); } catch { /* 終了を続ける */ }
      c.detach();     // tmux セッションは24時間残す（次回に再接続する）
    }
    host.closeAll();
  });
}

module.exports = { registerIpcHandlers, spawnSpec, lineEmitter, stripAnsi, cleanAnswer, withAttachments, turnSpec, executionSpec, concreteCli, sameLaunch, presentSession, sweepTerminalSessions, runPrompt, normalizeRepoUrl };
