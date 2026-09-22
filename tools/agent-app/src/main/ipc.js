'use strict';

const { ipcMain, dialog, shell, app, safeStorage } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const agentCli = require('./agentCli');
const cliSession = require('./cliSession');
const store = require('./store');
const git = require('./git');
const files = require('./files');
const host = require('./host');
const tmux = require('./tmux');
const worktree = require('./worktree');
const attachments = require('./attachments');
const cleanup = require('./cleanup');
const settings = require('./settings');
const sessionSetup = require('./sessionSetup');
const sessionExport = require('./sessionExport');
const { SessionBrowser } = require('./sessionBrowser');
const notify = require('./notify');
const { Updater } = require('./update');
const forkProtocol = require('../renderer/forkProtocol');
const response = require('./response');
const { createGate } = require('./executionGate');
const skills = require('./skills');
const skillRemoval = require('./skillRemoval');
const skillSelection = require('./skillSelection');
const herd = require('./herd');
const agentsMod = require('./agents');
const share = require('./share');
const { registerAutomationIpc, makeTaskCommandSpawnSpec } = require('./automation/ipc');
const runner = require('./automation/runner');
const judgeSetting = require('./judgeSetting');
const modelSelection = require('./modelSelection');
const selecting = new Map();
let selectionLimits = async () => ({ agentLimits: [] });
let selectionRatings = async () => '';
const attention = require('./attention');
const runHistory = require('./automation/run-history');
const agentFlow = require('./automation/agent-flow');
const machineStore = require('./automation/store');
const flowStore = require('./automation/flow-store');
const taskModel = require('./automation/model');
const requestRouting = require('./requestRouting');
const teaching = require('./automation/teaching');
const { stripAnsi, cleanAnswer, lineEmitter } = require('./text');
const { userData, requireRepo, distroFor, dirsOf, sessionDirs, mainBranch } = require('./paths');
const { spawnSpec, capture, killTree } = require('./proc');
const shareRun = require('./share/run');
const teachingIpcModule = require('./teachingIpc');
const { runPrompt, runSharedPrompt, normalizeRepoUrl, refreshRepoUrls, repoFor } = shareRun;
const audit = require('./audit');
const evaluation = require('./evaluation');
const artifactShare = require('./artifactShare');
const { SkillPublication, sourceOf: skillPublicationSource } = require('./skillPublication');
const skillCredentials = require('./skillCredentials');

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

// ---- ヘッドレス（1 ターン 1 プロセス）。tmux が無いときの代替 --------------------------

// 走っているヘッドレスのターン。セッション ID → 子プロセス。
const running = new Map();
const turnGate = createGate();
const instanceId = crypto.randomUUID();

function emitResponseParts(send, id, added) {
  for (const item of (added && added.thinking) || []) send('turn:progress', { id, item });
  for (const item of (added && added.information) || []) send('turn:info', { id, item });
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
  if (!text && !(p.attachments || []).length) throw new Error('依頼内容を入力してください');
  return { cli, model, readonly, autoApprove, text };
}

// 保存済みの起動方針を、そのターンで実際に使う CLI / model へ解決する。
// policy を持たない旧画面・旧セッションは、それまでの直接指定の意味を保つ。
//   optimized … 「エージェントを最適化する」が効いているか（設定 × herd の有無）。false なら節約 /
//               品質重視は「おすすめ」として解決する（settings.effectivePolicy）
function executionSpec(sess, p, config, { optimized = true, agents = null } = {}) {
  // Global allocation changes and expiry only affect new work, never an existing conversation.
  if (p.policy && !['shared', 'direct'].includes(p.policy) && p.policy === sess.policy && (!p.cli || p.cli === sess.cli)) {
    return { ...turnSpec(sess, { ...p, cli: sess.cli, model: sess.model }), policy: sess.policy, tier: sess.tier, source: 'session' };
  }
  const legacyDirect = !p.policy;
  const selected = settings.resolve(config, legacyDirect ? {
    policy: 'direct', cli: p.cli || sess.cli, model: p.model != null ? p.model : sess.model,
  } : p, { optimized, agents });
  const base = turnSpec(sess, { ...p, cli: selected.cli, model: selected.model });
  return { ...base, policy: selected.policy, tier: selected.tier, source: selected.source };
}

// `herd`（一族の 1 語）を、このターンで実際に起こす定義へ写す。requested に元の名前を残す。
// 起こすのは常に一族の共通 TUI（agent-herd の既定バックエンド）で、用途は本文の先頭に置く
// スラッシュ行（slash）で表す——ターンごとに CLI を入れ替えない。
//   agents      … listAgents の結果（ホストで使えるかの印つき）
//   attachments … 添付（作業フォルダの中のファイルがあれば /edit）
function concreteCli(spec, agents, { attachments = [] } = {}) {
  const virtual = herd.isHerd(spec.cli);
  const selectedMember = spec.autoSelected && herd.isMember(agents.find(a => a.name === spec.cli));
  if (!virtual && !selectedMember) return { ...spec, requested: spec.cli, family: '', slash: '' };
  const purpose = herd.purposeOf({ readonly: spec.readonly, workFiles: herd.hasWorkFiles(attachments), answerOnly: spec.answerOnly });
  const picked = herd.resolveChat(purpose, virtual ? agents : agents.filter(a => a.name === spec.cli));
  return { ...spec, cli: picked.cli, requested: virtual ? herd.HERD : spec.cli, family: herd.HERD, slash: picked.slash, familyReason: picked.reason };
}

// 宣言で readonly を保証できる CLI か（`agents/*.json` の readonly）。best-effort は
// フラグを無視しても止まらないので、「答えるだけ」の依頼は配らない。
function readonlyEnforced(cli, repo) {
  try { return agentCli.load(cli, repo).readonly === 'enforced'; } catch { return false; }
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

async function runHeadless(id, turn, send) {
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
  let entry = store.cliEntry(sess, cli);
  if (spec.session?.kind === 'create' && !entry?.id) {
    try {
      const prepared = await cliSession.prepare({ cli, shell: host.shellFor(distroFor(repo)),
        cwd: dirs.hostDir, argv: [spec.command[0]], env: spec.env });
      if (prepared.sessionId) {
        store.setCliEntry(ud, id, cli, { id: prepared.sessionId, seen: 0 });
        entry = store.cliEntry(store.readSession(ud, id), cli);
      }
    } catch (error) { turn.setupWarning = [turn.setupWarning, error.message].filter(Boolean).join('\n'); }
  }
  // その CLI がまだ見ていない分だけ再送する（セッション ID が無い CLI は毎回ぜんぶ）
  const unseen = entry && entry.id ? history.slice(entry.seen) : history;
  const cmd = agentCli.turnCmd(spec, {
    prompt, model, readonly, cliSession: entry ? entry.id : '', history: unseen, files: attFiles,
    allowContinue: !(sess.origin && sess.origin.repo === sess.repo),
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
  child.cli = cli;
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
    audit.feedTurn(ud, { session: sess, message, sessionId: sid });
    if (evaluator) evaluator.noteTurn({ session: sess, message, sessionId: sid });
    turn.release();
    send('turn:done', { id, message });
  });
  return { pid: child.pid, argv: cmd.argv };
}

let shareInstance = null;
// 応答と実行の評価（evaluation.js）。registerIpcHandlers で作る。
let evaluator = null;

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
    mode: 'read', model: requested.model, priority: p.priority || 'normal', to: p.to || '', attachments: served, workspace,
  }, { onDone: release });
  store.updateSession(ud, id, {
    cli: requested.cli || sess.cli, model: requested.model, readonly: true, policy: settings.SHARED_POLICY, tier: '', transport: 'headless',
    share: { id: request.id },
  });
  send('turn:started', { id, argv: [], warning: '' });
  send('turn:progress', { id, item: { text: `${request.to ? `${request.to} 宛てに` : ''}共有の列に並べた（${request.id}）`, status: 'running' } });
  for (const item of skillDelivery.information) send('turn:info', { id, item });
  return { pid: 0, argv: [], shared: request.id };
}

// ---- tmux（対話起動）。会話 ID → Conversation ----------------------------------------

const conversations = new Map();
const openingConversations = new Map();

// UI の表示と送信が重なっても、同じ会話の起動・ID発行は直列に行う。
function openConversation(id, send, options = {}) {
  const previous = openingConversations.get(id) || Promise.resolve();
  const opening = previous.catch(() => {}).then(() => openConversationNow(id, send, options));
  openingConversations.set(id, opening);
  const cleanup = () => { if (openingConversations.get(id) === opening) openingConversations.delete(id); };
  opening.then(cleanup, cleanup);
  return opening;
}

function sameLaunch(a, b) {
  return !!a && !!b && a.cli === b.cli && String(a.model || '') === String(b.model || '')
    && Boolean(a.readonly) === Boolean(b.readonly) && Boolean(a.autoApprove) === Boolean(b.autoApprove);
}

// tmux セッションを（無ければ起動して）持つ。
//   launch … { cli, model, readonly }。ターンが指定する。動いているもの（existing / sess.live）と
//            違えば起動し直す（モデルやエージェントを変えたターン）
//   fresh  … 残っているセッションを消して、会話の「次のターン」の既定で起動し直す（「再起動」）
//   どちらも無し（会話を開いただけ）… 動いているものにつなぐだけで、起動し直さない
async function openConversationNow(id, send, { cols, rows, fresh = false, launch = null } = {}) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  if (!launch && modelSelection.pending(sess)) throw new Error('最初の依頼を送るとAIを自動選択します');
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
  const { distro, shell } = host.hostOf(repo, cfg.wslDistro, { lane: 'terminal' });
  const cwd = dirsOf(sess.repo, sess.worktree || '', { mustExist: true }).hostDir;
  const info = await host.probe(distro, { lane: 'terminal' });
  if (!info.ok) throw new Error(info.error || 'ホストのシェルを起動できません');
  if (!info.tmux) throw new Error(process.platform === 'win32' ? 'WSL に tmux が見つかりません（sudo apt install tmux）' : 'tmux が見つかりません');
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  // The receipt may have arrived while Electron was closed or immediately before restart.
  const captureOptions = { shell, home: info.home, id, cli: want.cli, cwd };
  async function syncSession() {
    const sid = await cliSession.read(captureOptions);
    if (!sid) return;
    const saved = store.readSession(ud, id);
    if (store.cliEntry(saved, want.cli)?.id !== sid) store.setCliEntry(ud, id, want.cli, { id: sid });
  }
  if (existing?.syncSession) await existing.syncSession();
  await syncSession();
  const entry = store.cliEntry(store.readSession(ud, id), want.cli);
  const cmd = agentCli.interactiveCmd(spec, {
    model: want.model, readonly: want.readonly, autoApprove: want.autoApprove,
    cliSession: entry ? entry.id : '', history,
    allowContinue: !(sess.origin && sess.origin.repo === sess.repo),
  });
  let captureWarning = '';
  let lastSync = 0;
  const conv = new tmux.Conversation({
    prepareLaunch: async () => {
      try {
        const prepared = await cliSession.prepare({ ...captureOptions, argv: cmd.argv, env: cmd.env });
        if (prepared.sessionId) store.setCliEntry(ud, id, want.cli, { id: prepared.sessionId, seen: 0 });
        return prepared;
      }
      catch (err) { captureWarning = err.message; return { argv: cmd.argv, env: cmd.env }; }
    },
    syncSession: async () => {
      if (Date.now() - lastSync < 1000) return;
      lastSync = Date.now();
      await syncSession();
    },
    id, shell, cwd, argv: cmd.argv, env: cmd.env, patterns: tmux.compilePatterns(spec.interactive), cols, rows, launch: want,
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
  const warning = [cmd.readonlyWarning, captureWarning, opened.reused ? '' : cmd.warning].filter(Boolean).join('\n');
  return { name: conv.name, phase: conv.phase, detail: conv.detail, reused: opened.reused, restarted: !opened.reused, warning, argv: cmd.argv, launch: want };
}

async function closeConversation(id) {
  const conv = conversations.get(id);
  if (conv) { await conv.syncSession?.(); conversations.delete(id); await conv.kill(); }
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
  const { cli, model, readonly, autoApprove, atts, policy, tier, selectedSkills, family = '', slash = '' } = turn;
  let { text, prompt } = turn;
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
  const sess = store.readSession(ud, id);
  const history = sess.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
  // 共通 TUI は各依頼を独立実行する。端末が生きていてもモデルに履歴は残らない。
  const retainsContext = turn.spec.interactive?.retainsContext !== false;
  const unseen = retainsContext ? history.slice(conv.seen) : history;
  // CLI が入力可能になるまで待ってから判断する。tmux の存在だけでは復元の根拠にしない。
  // 初回の作成依頼は残し、既存編集の再開説明だけを省く。未共有の履歴や今回の指示は届ける。
  if (turn.resumeContext !== undefined && conv.resumed && retainsContext) {
    if (!turn.resumeContext && !unseen.length) {
      turn.release();
      return { name: conv.name, started: false, restarted: !!(opened && opened.restarted), warning: opened?.warning || '' };
    }
    text = turn.resumeContext || '未共有のやり取りを踏まえて編集を続けてください。';
    prompt = turn.resumeContext ? turn.resumedPrompt : text;
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
  // 用途のスラッシュ行は（履歴の再送があっても）本文の一番上。共通 TUI は先頭の /name 行だけを読む
  const full = herd.withSlash(slash, unseen.length ? agentCli.replayPrompt(unseen, prompt, { resumed: conv.resumed && retainsContext }) : prompt);
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
    audit.feedTurn(ud, { session: sess, message });
    if (evaluator) evaluator.noteTurn({ session: sess, message });
    turn.release();
    send('turn:done', { id, message });
  });
  const warning = [turn.setupWarning, opened ? opened.warning : ''].filter(Boolean).join('\n');
  send('turn:started', { id, argv: [], warning });
  send('turn:progress', { id, item: { text: `${cli} が依頼を処理しています`, status: 'running' } });
  for (const item of turn.setupInformation || []) send('turn:info', { id, item });
  return { name: conv.name, started: true, restarted: !!(opened && opened.restarted), warning };
}

// いま応答中の会話（ヘッドレスの子プロセスと、ターン中の tmux 会話）。混線の注意（continueClashWarning）に使う
function activeTurns(ud) {
  const out = [];
  const seen = new Set();
  const push = (id, cli) => {
    if (seen.has(id)) return;
    seen.add(id);
    let sess;
    try { sess = store.readSession(ud, id); } catch { return; }
    out.push({ id, repo: sess.repo, cli: cli || sess.cli || '', name: sess.name || '' });
  };
  for (const [id, child] of running) push(id, child.cli);
  for (const conv of conversations.values()) if (conv.turn) push(conv.id, conv.launch && conv.launch.cli);
  return out;
}

// 1 ターン。CLI・モデル・モードはターンごとに決め、tmux か ヘッドレスかもここで決める
// （対話定義を持つ CLI で tmux が使えるなら tmux）。
async function runTurn(id, p, send, { config = null, release = () => {}, resumeContext } = {}) {
  const ud = userData();
  const sess = store.readSession(ud, id);
  const repo = requireRepo(sess.repo);
  const dirs = dirsOf(sess.repo, sess.worktree || '', { mustExist: true });
  const cfg = config || store.loadConfig(ud);
  const preparing = (text) => send('turn:progress', { id, item: { text, status: 'running', preparing: true } });
  preparing('準備中…\n利用できるエージェントを確認しています。');
  const agents = await listAgents(repo);
  let requested = executionSpec(sess, p, cfg, { agents, optimized: settings.optimized(cfg, { herdAvailable: agentsMod.herdAvailable(agents) }) });
  if (requested.policy === settings.SHARED_POLICY) return runShared(id, sess, dirs, p, requested, cfg, send, release);
  // 振り分けの答えに依らない支度は、判定を待たずに始める。Windows では 1 件ごとに
  // wsl.exe の起動が乗るので、直列にすると判定の後ろへ数秒積む。
  const autoSelecting = modelSelection.pending(sess) && requested.policy !== 'direct';
  const limitsAhead = autoSelecting ? selectionLimits().catch(() => ({ agentLimits: [] })) : null;
  const ratingsAhead = autoSelecting ? selectionRatings().catch(() => '') : null;
  // tmux で起こすなら host.probe が要る（transport の判定と openConversation の両方。
  // probe の写しは distro ごとで lane を分けないので、1 回温めれば両方に効く）。
  const probeAhead = cfg.transport === 'tmux' ? host.probe(distroFor(repo)).catch(() => ({ ok: false })) : null;
  const selectionConfig = cfg.instructions.skillSelection || {};
  const skillMode = p.skillMode || selectionConfig.defaultMode || 'auto';
  // 依頼の振り分け（agent-herd route）。会話だけが対象で、タスク・ワークフローを AI と作る会話は
  // 振り分けない。決めなければ従来どおり（会話で実行、スキルは文字列の一致）。
  const askedReadonly = requested.readonly;
  let routed = null;
  const routingSkip = sess.kind !== 'conversation' ? 'kind'
    : requestRouting.skipReason({ text: requested.text, mode: p.routing, skillMode, quickRequests: cfg.instructions.quickRequests });
  if (!routingSkip) {
    preparing('判定中…\n依頼の内容から、会話・タスク・ワークフローの進め方を判定しています。');
    const safe = (read) => { try { return read(); } catch { return []; } };
    const names = selectionConfig.enabled === false ? [] : (selectionConfig.candidates || []);
    const cands = requestRouting.candidates({
      text: requested.text, repo: path.basename(repo), readonly: requested.readonly,
      attachments: (Array.isArray(p.attachments) ? p.attachments : []).map((a) => a.name || a.rel || ''),
      tasks: safe(() => machineStore.list(repo)).map((t) => ({ id: t.machine, name: t.name, description: t.description })),
      flows: safe(() => flowStore.list(repo)).filter((f) => f.valid !== false).map((f) => ({ id: f.id, name: f.name, description: f.description })),
      skills: skills.catalog(repo).filter((skill) => names.includes(skill.name)),
    });
    const controller = new AbortController();
    selecting.set(id, controller);
    const routeStartedAt = Date.now();
    try {
      routed = await requestRouting.route({
        text: requested.text, candidates: cands, cwd: dirs.fsDir, signal: controller.signal,
        file: path.join(ud, 'routing', `${id}.json`), toHostPath: process.platform === 'win32' ? host.toWslPath : undefined,
        capture: (name, args, opts) => runner.capture(name, args, { ...opts, spawnSpec: makeTaskCommandSpawnSpec(userData)(name) || undefined }),
      });
    } finally { selecting.delete(id); }
    if (controller.signal.aborted) throw new Error('振り分けを停止しました');
    // 判定 1 回に付き観測行 1 行（hold の真偽・決めたかによらず）。実会話の確度分布はここに溜まる。
    audit.feedRouting(ud, { sessionId: id, routed, seconds: (Date.now() - routeStartedAt) / 1000 });
    preparing(`振り分け完了\n${requestRouting.information(routed).title}`);
    if (routed.hold) {
      // 会話は送らない。案内を 1 枚残して、開く / そのまま会話で実行 は人が選ぶ。
      // タスクの流用なら、実行条件（{{key}}）を依頼から写しておく（日付は決定的、残りはローカル LLM の extract）
      let inputs = {};
      if (routed.handling.choice === 'task') {
        preparing('判定中…\nタスクに渡す入力値を依頼から読み取っています。');
        let parameters = [];
        try { parameters = taskModel.normalizeProcedure(machineStore.read(repo, routed.target.id).raw).parameters || []; } catch { /* 読めない定義は入力なし */ }
        inputs = await requestRouting.extractInputs({
          text: requested.text, parameters, cwd: dirs.fsDir,
          capture: (name, args, opts) => runner.capture(name, args, { ...opts, spawnSpec: makeTaskCommandSpawnSpec(userData)(name) || undefined }),
        });
      }
      const held = requestRouting.heldMessage(routed, { text: requested.text, attachments: p.attachments || [], inputs });
      store.appendMessage(ud, id, held.message);
      release();
      return { held: { notice: held.notice }, acceptedAt: new Date().toISOString() };
    }
    if (routed.handling && routed.handling.choice === 'answer') requested = { ...requested, readonly: true, answerOnly: true };
  }
  let chosen = null;
  if (autoSelecting) {
    preparing('判定中…\n依頼に合うエージェントとモデルを選択しています。');
    const controller = new AbortController();
    selecting.set(id, controller);
    try {
      const [limits, ratings] = await Promise.all([
        limitsAhead || selectionLimits().catch(() => ({ agentLimits: [] })),
        ratingsAhead || selectionRatings().catch(() => ''),
      ]);
      if (controller.signal.aborted) throw new Error('自動選択を停止しました');
      chosen = await modelSelection.select({ config: cfg, agents: sess.kind === 'conversation' ? agents : agents.filter(a => a.interactive || a.virtual), load: cli => agentCli.load(cli, repo),
        prompt: requested.text || (p.attachments || []).map(a => a.name || a.rel || '').join('\n'),
        readonly: requested.readonly, attachments: p.attachments, cwd: dirs.fsDir, observed: limits.agentLimits, ratings, workload: 'chat',
        signal: controller.signal, capture: (name, args, opts) => runner.capture(name, args, {
          ...opts, spawnSpec: makeTaskCommandSpawnSpec(userData)(name) || undefined,
        }),
      });
      requested = { ...requested, cli: chosen.cli, model: chosen.model, source: 'auto' };
    } finally { selecting.delete(id); }
  }
  requested.autoSelected = !!(chosen || sess.modelSelection) && requested.policy !== 'direct';
  let base = concreteCli(requested, agents, { attachments: p.attachments });
  // 「答えるだけ」は readonly を宣言で保証できる CLI にしか配らない。enforced が 1 つも無ければ
  // answer の約束を取り下げ、利用者が選んだ権限へ戻す（黙って読み取り専用を名乗らない）。
  let answerSwap = null;
  if (requested.answerOnly && !readonlyEnforced(base.cli, repo)) {
    const enforced = agents.find((item) => item.available && !item.virtual && readonlyEnforced(item.name, repo));
    requested = enforced
      ? { ...requested, cli: enforced.name, model: '', source: 'answer-readonly' }
      : { ...requested, readonly: askedReadonly, answerOnly: false };
    answerSwap = { from: base.cli, to: enforced ? enforced.name : '' };
    base = concreteCli(requested, agents, { attachments: p.attachments });
  }
  preparing(`準備中…\n${[base.cli, base.model].filter(Boolean).join(' / ')} を起動しています。`);
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
    const info = await (probeAhead || host.probe(distroFor(repo)));
    if (info.ok && info.tmux) transport = 'tmux';
  }
  const attached = withAttachments(ud, base.text, p.attachments, dirs);
  let setupInformation = [
    ...(routed ? [requestRouting.information(routed)] : []),
    ...(answerSwap ? [{ type: 'status', status: answerSwap.to ? 'success' : 'warning',
      title: answerSwap.to
        ? `読み取り専用を保証できる ${answerSwap.to} で答えます`
        : '読み取り専用を保証できる CLI が無いので、通常の権限で実行します',
      detail: `${answerSwap.from} の readonly は best-effort（宣言を無視しても止まらない）` }] : []),
    ...(routed && routed.routine && routed.routine.value ? [requestRouting.routineInformation()] : []),
    ...familyInfo, ...(chosen ? [modelSelection.information(chosen)] : []),
  ];
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
    setupInformation.push(...startup.information);
    setupWarning = [plan.warning, startup.warning].filter(Boolean).join('\n');
    setupSkills = plan.skills;
    store.setCliEntry(ud, id, base.cli, { setupApplied: true });
  }
  const selectedSkills = skillSelection.select({
    mode: skillMode, judged: routed && routed.decided ? routed.skills : null,
    text: [base.text, ...(Array.isArray(p.attachments) ? p.attachments.map((item) => item.name || item.rel || '') : [])].join('\n'),
    requested: p.skills,
    candidates: selectionConfig.enabled === false ? [] : selectionConfig.candidates,
    catalog: skills.catalog(repo),
  });
  const skillDelivery = skillSelection.deliver(selectedSkills, spec);
  setupInformation.push(...skillDelivery.information);
  // 直前のセッションを拾う CLI が同じリポジトリで並行していれば、送る前に 1 行出す（止めない）
  const clash = agentCli.continueClashWarning({ id, repo: sess.repo, cli: base.cli, spec }, activeTurns(ud));
  if (clash) {
    setupWarning = [setupWarning, clash].filter(Boolean).join('\n');
    setupInformation.push({ type: 'status', title: clash, status: 'attention' });
  }
  setupSkills.push(...skillDelivery.commands.map((command) => ({
    command, name: command.replace(/^[$/]+/, ''), onError: selectedSkills.mode === 'manual' ? 'fail' : 'warn',
  })));
  // ヘッドレスには対話セッションが無いため、先頭のコマンドブロックとして同じ実行へ載せる。
  // tmux は runTmux が 1 件ずつ先に送るので、本依頼へ混ぜない。
  // 「会話」だけ、別のリポジトリへ分岐する作法（@fork 行）を添える。タスクを AI と作る会話には添えない
  const instructedPrompt = sessionSetup.withInstructions(attached.prompt, cfg.instructions, {
    answerOnly: !!base.answerOnly,
    artifacts: !base.answerOnly,
    fork: !base.answerOnly && sess.kind === 'conversation' ? { repos: cfg.repos, current: sess.repo } : null,
  });
  const contextualPrompt = skillDelivery.instruction ? `${skillDelivery.instruction}\n\n${instructedPrompt}` : instructedPrompt;
  const prompt = transport === 'headless' && setupSkills.length
    ? `${setupSkills.map((item) => item.command).join('\n')}\n\n${contextualPrompt}`
    : contextualPrompt;
  const resumedPrompt = resumeContext ? sessionSetup.withInstructions(resumeContext, cfg.instructions) : '';
  const turn = { ...base, resumeContext, resumedPrompt, prompt, atts: attached.atts, files: attached.files, spec, setupInformation, setupWarning, setupSkills, selectedSkills, release };
  // 次のターンの既定として覚える（画面はこれを出す）。`herd` は写した先ではなく要求した
  // 名前のまま残す——次のターンは添付の有無でまた選び直す
  store.updateSession(ud, id, {
    // 振り分けの「答えるだけ」はこのターンだけ読み取り専用にし、次のターンの既定には残さない
    cli: base.requested || base.cli, model: base.model, readonly: routed && routed.handling && routed.handling.choice === 'answer' ? askedReadonly : base.readonly, autoApprove: base.autoApprove,
    policy: base.policy, tier: base.tier, transport,
    ...(chosen ? { modelSelection: chosen } : {}),
  });
  // 起動先が決まったことを画面へ知らせる。tmux なら、開始スキルや依頼の送信を待たずに端末を
  // 出せる——待ちは変わらないが、待っている間に何が起きているかが見える。
  send('turn:transport', { id, transport, cli: base.cli, model: base.model });
  if (transport === 'headless') {
    // ヘッドレスの CLI へ移るなら、動いていた tmux の CLI は止める（同時に 2 つは持たない）
    if (conversations.has(id) || sess.live) await closeConversation(id);
    return runHeadless(id, turn, send);
  }
  return runTmux(id, turn, send);
}

async function guardedRunTurn(id, p, send, { resumeContext } = {}) {
  const cfg = store.loadConfig(userData());
  turnGate.acquire(id, cfg.execution.maxConcurrent);
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    turnGate.release(id, cfg.execution.maxConcurrent);
  };
  try {
    return await runTurn(id, p, send, { config: cfg, release, resumeContext });
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

// タスク・ワークフローを AI と作る会話。会話の実行（tmux・同時実行枠）はここから渡す
const teachingIpc = teachingIpcModule.create({
  presentSession,
  appRoot: automationAppRoot,
  busy: (id) => running.has(id) || !!conversations.get(id)?.turn,
  queuedTurnIds: (max) => turnGate.snapshot(max).ids,
  runTurn: (id, payload, send, options) => guardedRunTurn(id, payload, send, options),
});
const {
  teachingTools, launchTeachingBrowser, teachingBrowserPage, taskConversationView,
  prepareTeachingView, startTeaching, demonstrate,
  flowConversationView, prepareFlowTeaching, startFlowTeaching, adoptFlowDraft,
} = teachingIpc;

function registerIpcHandlers(getWindow) {
  const post = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };
  const sessionTitle = (id) => {
    try { return store.readSession(userData(), id).title || ''; } catch { return ''; }
  };
  // 画面が前面に無いときだけ、終わったこと・聞かれていることを OS の通知で知らせる（notify.js）。
  const notifier = notify.createNotifier({
    getWindow,
    enabled: () => store.loadConfig(userData()).notify.background !== false,
    open: (event) => { if (event.id) post('notify:open', { id: event.id }); },
  });
  // 知らせる合図は、既に renderer へ流している 2 つ（ターンの終わり・phase の変化）から拾う。
  // 通知のためだけの経路は作らない。
  const send = (channel, payload) => {
    post(channel, payload);
    if (channel === 'turn:done') {
      notifier.show({ kind: notify.turnKind(payload.message || {}), name: sessionTitle(payload.id), id: payload.id });
    } else if (channel === 'term:phase' && payload && payload.phase === 'attention') {
      notifier.show({ kind: 'attention', name: sessionTitle(payload.id), id: payload.id });
    }
  };
  registerAutomationIpc({
    selectionLimits: () => selectionLimits(),
    selectionRatings: () => selectionRatings(),
    getWindow,
    userData,
    appRoot: automationAppRoot(),
    onRunExit: ({ name, mode, result }) => {
      if (mode !== 'run') return;
      notifier.show({ kind: notify.taskRunKind(result || {}), name });
    },
  });
  handle('automation:teach:prepare', (p) => prepareTeachingView(p));
  handle('automation:teach:start', (p) => startTeaching(p, send));
  handle('automation:teach:session', (p) => taskConversationView(userData(), requireRepo(p.repo), String(p.machine || '').trim()));
  handle('automation:teach:demonstration', (p) => demonstrate(p));
  // ワークフローを AI と作る会話（タスクの 3 つと同じ形）
  handle('automation:flow:teach:prepare', (p) => { const r = prepareFlowTeaching(p); return { ...flowConversationView(r.ud, r.repo, r.id), existing: r.existing }; });
  handle('automation:flow:teach:start', (p) => startFlowTeaching(p, send));
  handle('automation:flow:teach:session', (p) => flowConversationView(userData(), requireRepo(p.repo), String(p.workflowId || '').trim()));
  handle('automation:flow:teach:adopt', (p) => adoptFlowDraft(p));
  handle('automation:teach:browser', (p) => launchTeachingBrowser(p));
  handle('automation:teach:browser:page', () => teachingBrowserPage());
  // 写したが送らずに閉じた添付を掃除する
  try { attachments.sweep(userData(), store.readAllSessions(userData())); } catch { /* 消せなくても動く */ }

  // 保存データの整理（cleanup.js）。数えるのはいつでも、消すのは利用者が選んだ種類だけ。
  function cleanupInput() {
    const ud = userData();
    return { userData: ud, sessions: store.readAllSessions(ud), repos: store.loadConfig(ud).repos };
  }
  handle('cleanup:scan', () => cleanup.scan(cleanupInput()));
  handle('cleanup:remove', (p) => {
    const ud = userData();
    const result = cleanup.remove(cleanupInput(), Array.isArray(p && p.keys) ? p.keys : [],
      { clearSnapshots: (id) => store.dropTerminalSnapshots(ud, id) });
    return { ...result, scan: cleanup.scan(cleanupInput()) };
  });

  // 自動更新（update.js）。確認は起動時・定期・手動、取り込みは利用者が押したときだけ。
  const updater = new Updater({
    userData: userData(),
    appVersion: app.getVersion(),
    loadConfig: () => store.loadConfig(userData()),
    shellFor: (distro) => host.shellFor(distro),
    post,
    quit: () => app.quit(),
  });
  handle('update:status', () => updater.status());
  handle('update:check', () => updater.check({ manual: true }));
  handle('update:apply', (p) => updater.apply({ app: !!p.app, tools: !!p.tools }));
  updater.schedule();

  // 監査（audit.js）。収集と判定はホスト側の agent-audit が行い、ここは周期と表示だけ。
  // 本人のターンが動いている間は回さない（busy）。
  const auditor = new audit.Auditor({
    userData: userData(),
    loadConfig: () => store.loadConfig(userData()),
    shellFor: (distro) => host.shellFor(distro, { lane: 'audit' }),
    post,
    busy: () => running.size > 0 || conversations.size > 0,
  });
  const artifacts = new artifactShare.ArtifactShare({
    userData: userData(),
    loadConfig: () => store.loadConfig(userData()),
    loadToken: () => skillCredentials.readToken(store.loadConfig(userData()), safeStorage),
    shellFor: (distro) => host.shellFor(distro),
    runPrompt,
  });
  const skillPublication = new SkillPublication({
    userData: userData(), shell: () => artifacts.shell(),
    loadAuth: () => ({ url: artifacts.config().shareRepo, token: artifacts.loadToken() }),
  });
  // 応答と実行の評価（evaluation.js）。判定 AI と伏せ字化は agent-herd / agent-audit（Windows では WSL 経由）。
  const toolCapture = (name, args, opts = {}) => runner.capture(name, args, {
    ...opts, spawnSpec: makeTaskCommandSpawnSpec(userData)(name) || undefined,
  });
  const sessionBrowserRef = { current: null };   // 検索の読み口。sessionBrowser は下で作る
  evaluator = new evaluation.Evaluator({
    userData: userData(),
    loadConfig: () => store.loadConfig(userData()),
    capture: toolCapture,
    runPrompt,
    readRecord: (key) => sessionBrowserRef.current.read(key),
    busy: () => running.size > 0 || conversations.size > 0,
    post,
  });
  // 実行（タスク・ワークフロー）は run-history が申告するので、その聞き手として評価に回す。
  audit.onFeed((rec, raw, extra) => { if (rec.workload === 'task' && extra && extra.record) evaluator.noteRun(extra); });
  handle('evaluation:status', () => evaluator.status());
  handle('evaluation:batch', (p) => evaluator.startBatch({ keys: p.keys, cli: p.cli, model: p.model }));
  // 課題（agent-audit の洞察）を会話へ渡す。依頼文を組む（mark: false）／渡し終えたら洞察に exported を
  // 書かせる（mark: true。書くのは agent-audit）。ダイアログを閉じただけの課題は消さない。
  handle('insight:handoff', async (p) => {
    const id = String(p.id || '');
    const source = attention.insightSources(audit.insights(userData(), { limit: Infinity })).find((item) => item.issue && item.issue.id === id);
    if (!source) throw new Error('その課題は見つかりません（渡した、または反証されたものは出しません）');
    const prompt = evaluation.handoffPrompt(source.issue);
    if (!p.mark) return { id, title: source.title, prompt, exported: false, warning: '' };
    const res = await auditor.markExported(id);
    return { id, title: source.title, prompt, exported: !!res.ok, warning: res.ok ? '' : `受信箱から消せませんでした: ${res.error || ''}` };
  });
  // 課題の根拠（観測 → record）。会話（ref = 会話 ID）と成果物へ辿れる形にして返す。
  handle('insight:evidence', (p) => {
    const ud = userData();
    const cfg = store.loadConfig(ud);
    const ids = Array.isArray(p.observationIds) ? p.observationIds : [];
    return audit.evidenceOf(ud, ids).map((item) => {
      if (item.tool === 'agent-app' && (item.workload === 'chat' || item.purpose === 'chat') && item.ref) {
        try {
          const sess = store.readSession(ud, item.ref);
          if (sess && cfg.repos.includes(sess.repo)) return { ...item, kind: 'conversation', title: sess.title || '無題の会話', repo: sess.repo, id: sess.id };
        } catch { /* アプリ外の会話や消えた会話 */ }
      }
      if (item.artifact && item.artifact.name && ['task', 'workflow'].includes(item.artifact.kind)) {
        const base = item.artifact.origin.replace(/^repo:/, '');
        const repo = cfg.repos.find((r) => String(r).split(/[\\/]/).filter(Boolean).pop() === base) || '';
        return { ...item, kind: item.artifact.kind === 'workflow' ? 'workflow' : 'task', title: item.artifact.name, repo, id: item.artifact.name };
      }
      return { ...item, kind: 'external', title: '', repo: '', id: '' };
    });
  });
  handle('audit:status', () => ({ ...auditor.status(), share: artifacts.list() }));
  handle('audit:run', () => auditor.run({ manual: true }));
  handle('audit:summary', (p) => auditor.summary({ by: p && p.by, period: p && p.period }));
  handle('audit:limits', () => auditor.limits());
  selectionLimits = () => auditor.limits();
  selectionRatings = () => auditor.ratings();
  handle('audit:manualLimit', (p) => {
    const allocation = require('../shared/allocation');
    const cfg = store.loadConfig(userData());
    const rows = allocation.manualLimits([{ ...p, observed_at: new Date().toISOString() }]);
    if (!rows.length || !allocation.validLimit(rows[0])) throw new Error('残量と未来のリセット日時を入力してください');
    return store.saveConfig(userData(), { audit: { ...cfg.audit,
      manualLimits: [...cfg.audit.manualLimits.filter(x => x.agent_cli !== rows[0].agent_cli), rows[0]] } });
  });
  // 成果物の行が持つ出所（`repo:<名前>`）から、登録済みリポジトリを引く。1 つに定まらない
  // ときは断る——別のリポジトリの定義を勝手に触らない。
  function repoOf(p) {
    if (p && p.repo) return requireRepo(p.repo);
    const name = String((p && p.origin) || '').replace(/^repo:/, '').trim();
    const repos = (store.loadConfig(userData()).repos || []).filter((r) => path.basename(r) === name);
    if (repos.length !== 1) throw new Error(`成果物のリポジトリを決められません（出所: ${name || '不明'}）`);
    return requireRepo(repos[0]);
  }
  // 実測の判定（基準を満たす / 様子見 / 使わない）。公開の画面はこれを添えて改善を出せる。
  // 一覧では 1 件ごとに読み直さない——judged() で 1 回読み、その表を使い回す。
  const NO_VERDICT = { verdict: '', failureModes: [], samples: 0, passed: 0 };
  function judged() {
    const rows = audit.artifacts(userData()).items || [];
    return new Map(rows.map((item) => [`${item.kind}/${item.name}`, {
      verdict: String(item.status || ''), failureModes: item.failure_modes || [],
      samples: item.samples || 0, passed: item.passed || 0,
    }]));
  }
  function verdictOf(kind, name) { return judged().get(`${kind}/${name}`) || NO_VERDICT; }
  function publishState(repo, kind, name, verdicts) {
    const measured = (verdicts || judged()).get(`${kind}/${name}`) || NO_VERDICT;
    return { ...artifacts.state({ repo, kind, name, verdict: measured.verdict }), ...measured };
  }
  // 公開先が入っているか（画面は入っていなければ公開の操作を出さない）。
  handle('publish:configured', () => ({ configured: artifacts.configured() }));
  handle('publish:state', (p) => publishState(p.repo ? requireRepo(p.repo) : '', String(p.kind || ''), String(p.name || '')));
  // 設定 > スキル の一覧。AI を選ぶと「その AI の置き場 + 共通の置き場」を歩く。
  handle('publish:skills', async (p) => {
    const repo = p && p.repo ? requireRepo(p.repo) : '';
    const configured = artifacts.configured();
    const verdicts = judged();
    // リポジトリ内・共通の保存先とも、一覧で表示した実体を公開する。
    const roots = skills.sourceRoots(repo, String((p && p.agent) || ''));
    const catalog = skills.catalogFromRoots(roots);
    const published = await skillPublication.states(catalog, artifacts.config().shareRepo);
    const items = catalog.map((item) => {
      const base = item.place === 'repo' ? publishState(repo, 'skill', item.name, verdicts)
        : { ...NO_VERDICT, status: 'outside', canPublish: false, canImprove: false, configured };
      // 表示した実体と公開処理が読む正典が違う場合、別の同名スキルを公開させない。
      const actionable = !!skillPublicationSource(item);
      return { ...skillPublication.present(item, base, published.get(item.path), actionable), ...skillRemoval.describe(item, roots) };
    });
    return { repo, configured, items };
  });
  let removingSkills = false;
  handle('skills:remove', async (p) => {
    if (removingSkills) throw new Error('スキルの削除処理中です');
    removingSkills = true;
    try {
      const result = await skillRemoval.remove({
        keys: p.keys,
        roots: () => skills.sourceRoots(p.repo ? requireRepo(p.repo) : '', String(p.agent || '')),
        confirm: async (items) => {
          const answer = await dialog.showMessageBox(getWindow(), {
            type: 'warning', title: 'スキルを削除',
            message: `${items.length} 件のスキルをゴミ箱へ移動しますか？`,
            detail: items.map((item) => `${item.name}\n${item.deletePath}`).join('\n\n')
              + '\n\nスキルのフォルダ全体（コマンド形式はファイル）を移動します。共通のスキルは他のリポジトリでも使えなくなります。',
            buttons: ['キャンセル', 'ゴミ箱へ移動'], defaultId: 0, cancelId: 0, noLink: true,
          });
          return answer.response === 1;
        },
        trashItem: (target) => shell.trashItem(target),
      });
      if (result.removed.length) skillPublication.cache.clear();
      return result;
    } finally { removingSkills = false; }
  });
  // 公開と改善は押したときだけ（merge は人。ここは push までで止める）。
  handle('publish:submit', async (p) => {
    const options = {
      repo: repoOf(p), kind: String(p.kind || ''), name: String(p.name || ''),
      sessionId: String(p.sessionId || ''), force: !!p.force,
    };
    if (options.kind === 'skill' && artifacts.configured()) {
      let found = artifactShare.locate(options.repo, 'skill', options.name);
      if (p.publicationKey) {
        const item = skills.catalog(options.repo, String(p.agent || '')).find(item => item.name === options.name);
        const source = skillPublicationSource(item);
        if (!source || source.key !== p.publicationKey) throw new Error('スキルの保存先が変わりました。一覧を更新してください。');
        options.source = source;
        found = source;
      }
      if (found) {
        const item = { ...options, dir: found.full, path: path.join(found.full, 'SKILL.md') };
        const state = (await skillPublication.states([item], artifacts.config().shareRepo)).get(item.path);
        if (state?.status === 'unknown') throw new Error('公開先を確認できません。接続を確認して再試行してください。');
        if (state && state.versionComparison !== 'local-newer' && !options.force) return { skipped: 'not-newer' };
        if (state?.status === 'published' && !options.force) return { skipped: 'already', branch: state.branch };
        // 別の公開先・同名スキルの古いローカル履歴で、必要な公開をスキップしない。
        if (state && state.status !== 'published') options.force = true;
      }
    }
    const result = await artifacts.submit(options);
    skillPublication.cache.clear();
    return result;
  });
  handle('publish:improve', async (p) => {
    const cfg = store.loadConfig(userData());
    const repo = repoOf(p);
    const options = { optimized: settings.optimized(cfg, { herdAvailable: agentsMod.herdAvailable(await listAgents(repo)) }) };
    // 既定は節約（ローカル実行系があればそれ）。その tier が未設定なら会話の既定へ倒す。
    let selected;
    try { selected = settings.resolve(cfg, { policy: 'saving' }, options); }
    catch { selected = settings.resolve(cfg, {}, options); }
    const kind = String(p.kind || '');
    const name = String(p.name || '');
    // 証跡は押した側が渡さなくてよい。実測の失敗クラスをここで添える。
    const evidence = Array.isArray(p.evidence) && p.evidence.length ? p.evidence
      : verdictOf(kind, name).failureModes.map((mode) => ({ status: 'failed', error_class: mode }));
    return artifacts.improve({
      repo, kind, name, evidence,
      cli: String(p.cli || selected.cli), model: String(p.model != null ? p.model : selected.model),
    });
  });
  auditor.schedule();

  handle('host:info', async () => {
    const cfg = store.loadConfig(userData());
    const info = await host.probe(process.platform === 'win32' ? cfg.wslDistro : '');
    return { platform: process.platform, distro: cfg.wslDistro, ...info, socket: tmux.SOCKET };
  });
  handle('config:get', () => store.loadConfig(userData()));
  // 設定 > 実行制御「遷移や振り分けの判定」。値は agent-herd の設定ファイル（python が動く側の
  // ~/.agents）にあるので、agent-herd config に読み書きを頼む（Windows では WSL 経由）。
  const herdCapture = (name, args, opts = {}) => runner.capture(name, args, {
    ...opts, spawnSpec: makeTaskCommandSpawnSpec(userData)(name) || undefined,
  });
  handle('judge:get', () => judgeSetting.read({ capture: herdCapture }));
  handle('judge:set', (p) => judgeSetting.write({ capture: herdCapture, value: p && p.value, keepAlive: !(p && p.keepAlive === false) }));
  handle('config:problem', () => store.takeConfigProblem());
  handle('config:save', (p) => {
    const before = store.loadConfig(userData());
    const next = store.saveConfig(userData(), skillCredentials.preparePatch(p.patch, before.audit, safeStorage));
    if (JSON.stringify(before.audit) !== JSON.stringify(next.audit)) skillPublication.cache.clear();
    if (before.wslDistro !== next.wslDistro) { host.closeAll(); availCache.clear(); }
    if (JSON.stringify(before.update) !== JSON.stringify(next.update)) updater.schedule();
    if (JSON.stringify(before.audit) !== JSON.stringify(next.audit)) auditor.schedule();
    if (shareInstance) shareInstance.reconfigure(next).catch(() => {});
    if (JSON.stringify(before.repos) !== JSON.stringify(next.repos)) refreshRepoUrls().catch(() => {});
    return next;
  });

  // 共有（LAN の参加者に依頼を回す）。投函は turn:send の policy: 'shared'。ここは観測と調整だけ
  let shareAgentNames = [];
  const refreshShareCaches = async () => {
    try { shareAgentNames = (await listAgents('')).filter((a) => a.available && !a.virtual).map((a) => a.name); } catch { /* 次の周で */ }
    try { const info = await host.probe(process.platform === 'win32' ? store.loadConfig(userData()).wslDistro : ''); shareRun.setTmuxAvailable(info.ok && info.tmux); } catch { shareRun.setTmuxAvailable(false); }
    await refreshRepoUrls().catch(() => {});
  };
  shareInstance = new share.Share({ userData: userData(), config: store.loadConfig(userData()), send, runPrompt: runSharedPrompt, agents: () => shareAgentNames, repoFor,
    screen: async id => {
      const conv = conversations.get(id);
      if (conv) { const captured = await conv.capture(); if (captured.ok) return captured.screen.text; }
      return store.readSession(userData(), id).terminalSnapshots?.at(-1)?.screenText || '';
    } });
  refreshShareCaches().then(() => shareInstance.start()).catch((err) => { shareInstance.error = err.message; });
  const shareTimer = setInterval(() => { refreshShareCaches().catch(() => {}); }, 5 * 60 * 1000);
  if (shareTimer.unref) shareTimer.unref();
  handle('share:status', () => shareInstance.status());
  handle('share:publish', p => shareInstance.publish(String(p.id || '')));
  handle('share:unpublish', p => shareInstance.unpublish(String(p.id || '')));
  handle('share:publicRefresh', () => shareInstance.refreshPublic());
  handle('share:publicView', p => shareInstance.readPublic(String(p.key || ''), true, String(p.revision || '')));
  handle('share:publicSay', p => shareInstance.sayPublic(String(p.key || ''), p.text, p.messageId));
  handle('share:cancel', (p) => shareInstance.cancel(String(p.id || '')));
  handle('share:priority', (p) => shareInstance.setPriority(String(p.id || ''), p.priority));
  handle('share:target', (p) => shareInstance.setTarget(String(p.id || ''), p.to));
  handle('share:accept', (p) => shareInstance.accept(String(p.id || '')));
  handle('share:stop', (p) => shareInstance.stopAccepted(String(p.id || '')));
  handle('share:screen', (p) => shareInstance.screenOf(String(p.id || '')));
  // ひとこと（人と人）。CLI には入らない
  handle('share:say', (p) => shareInstance.say(String(p.id || ''), String(p.text || '')));
  // 引き受けた依頼の端末へキーを送る
  handle('share:keys', (p) => shareInstance.keys(String(p.id || ''), String(p.data || '')));
  // 引き受け方（自動で受ける / 選んで受ける / 受けない）。設定 > 共有と同じ値を書き換える
  handle('share:mode', async (p) => {
    const current = store.loadConfig(userData());
    const next = store.saveConfig(userData(), { share: { ...current.share, accept: String(p.mode || 'off') } });
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

  const sessionBrowser = new SessionBrowser({ userData, share: shareInstance });
  sessionBrowserRef.current = sessionBrowser;
  const summaryJobs = new Map();
  // 検索は見つかった端から流す（hit / progress）。打ち止めは invoke の戻り値。画面はページを持たない。
  const searchStream = requestId => event => {
    const [kind, payload] = Object.entries(event)[0];
    post('sessions:search:' + kind, { requestId, ...payload });
  };
  handle('sessions:search', p => sessionBrowser.search(p.query, p.requestId, searchStream(p.requestId)));
  handle('sessions:cancel', p => {
    sessionBrowser.cancel(p.requestId);
    const job = summaryJobs.get(p.requestId);
    if (job) { job.cancelled = true; job.active?.stop('引き継ぎを中止しました'); }
  });
  handle('sessions:read', p => sessionBrowser.read(String(p.key || '')));
  handle('sessions:export', async p => {
    const record = await sessionBrowser.read(String(p.key || ''));
    const sess = record.appId ? store.readSession(userData(), record.appId) : record;
    const out = sessionExport.write(userData(), sess);
    const error = await shell.openPath(out.path);
    return { name: out.name, warning: error ? `書き出したファイルを開けませんでした: ${error}` : '' };
  });
  handle('sessions:import', async p => {
    const folder = p.folder === true;
    const picked = await dialog.showOpenDialog(getWindow(), folder
      ? { title: '会話の保存フォルダを追加', properties: ['openDirectory'] }
      : { title: 'VS Code の会話ファイルを追加', properties: ['openFile', 'multiSelections'], filters: [{ name: 'VS Code の会話（JSON）', extensions: ['json', 'jsonl'] }] });
    if (picked.canceled) return null;
    (folder ? sessionBrowser.codeRoots : sessionBrowser.imports).push(...picked.filePaths);
    sessionBrowser.saveSources();
    return true;
  });
  handle('sessions:prepare', async p => {
    const repo = requireRepo(p.repo);
    if (!['handoff', 'fork'].includes(p.mode)) throw new Error('引き継ぐ方法を選んでください');
    const id = String(p.requestId || crypto.randomUUID());
    if (summaryJobs.has(id)) throw new Error('引き継ぎ内容を作成中です');
    const job = { active: null, cancelled: false };
    const cfg = store.loadConfig(userData());
    turnGate.acquire(id, cfg.execution.maxConcurrent);
    summaryJobs.set(id, job);
    try {
      const selected = concreteCli({ cli: p.cli, model: p.model, readonly: true }, await listAgents(repo));
      return await sessionBrowser.prepare({ ...p, repo, cli: selected.cli, model: selected.model }, async prompt => {
        if (job.cancelled) throw new Error('引き継ぎを中止しました');
        job.active = runPrompt({ cli: selected.cli, model: selected.model, prompt, readonly: true,
          repo, cwd: dirsOf(repo, '').fsDir, timeoutMs: 180000 });
        const result = await job.active.done;
        if (job.cancelled || result.error || result.code !== 0 || result.stopped) throw new Error(result.error || '引き継ぎ内容を作成できませんでした');
        return result.text;
      });
    } finally { summaryJobs.delete(id); turnGate.release(id, cfg.execution.maxConcurrent); }
  });
  handle('sessions:create', async p => {
    const plan = sessionBrowser.prepared.get(p.token);
    if (!plan) throw new Error('引き継ぎ内容を作り直してください');
    requireRepo(plan.repo);
    const agents = await listAgents(plan.repo);
    const agent = agents.find(a => a.name === plan.cli && a.available);
    if (!agent) throw new Error('このフォルダでは選択したエージェントを起動できません');
    const cfg = store.loadConfig(userData());
    const info = await host.probe(distroFor(plan.repo));
    const sameRepo = plan.record.appId && plan.record.repo === plan.repo;
    const preferredTransport = sameRepo ? plan.record.defaults?.transport || cfg.transport : cfg.transport;
    // A local fork shares its original working directory; validate it before creating the session.
    dirsOf(plan.repo, sameRepo ? plan.record.defaults?.worktree || '' : '', { mustExist: true });
    const transport = preferredTransport === 'tmux' && info.tmux && agent.interactive ? 'tmux' : 'headless';
    return sessionBrowser.create({ ...p, transport });
  });
  handle('session:recent', () => store.recentSessions(userData(), store.loadConfig(userData()).repos));
  handle('session:list', (p) => store.listSessions(userData(), p.repo || ''));
  handle('session:create', async (p) => {
    const repo = requireRepo(p.repo);
    const cfg = store.loadConfig(userData());
    const availableAgents = await listAgents(repo);
    const selected = settings.resolve(cfg, p.policy ? p : {
      policy: 'direct', cli: p.cli || cfg.execution.tiers.medium.cli, model: p.model,
    }, { agents: availableAgents, optimized: settings.optimized(cfg, { herdAvailable: agentsMod.herdAvailable(availableAgents) }) });
    let branch = '';
    if (p.worktree) branch = (await worktree.find(repo, p.worktree, distroFor(repo))).branch;
    return store.createSession(userData(), {
      ...p, repo, branch, cli: selected.cli, model: selected.model,
      policy: selected.policy, tier: selected.tier,
      allocation: selected.allocation,
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
    if (!prompt) throw new Error('分岐先への依頼を入力してください');
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
    selecting.get(p.id)?.abort();
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
    if (selecting.has(p.id)) { selecting.get(p.id).abort(); return true; }
    if (shareInstance && shareInstance.pendingSessionIds().includes(p.id)) { await shareInstance.cancelSession(p.id); return true; }
    const c = running.get(p.id);
    if (c) { c.stop(); return true; }
    const conv = conversations.get(p.id);
    return conv ? conv.stop() : false;
  });
  const runningTurnIds = () => [...new Set([
    ...turnGate.snapshot(store.loadConfig(userData()).execution.maxConcurrent).ids,
    ...running.keys(),
    ...[...conversations.values()].filter((c) => c.turn).map((c) => c.id),
    ...(shareInstance ? shareInstance.pendingSessionIds() : []),
  ])];
  handle('turn:running', () => runningTurnIds());

  // 受信箱（attention.js）。正典（会話の要約・タスクの実行履歴・ワークフローの実行）を読んで
  // 「未読」「要対応」を派生させる。ここで状態は持たず、書くのは「見た」（config.json の attentionSeen）だけ。
  handle('attention:list', () => {
    const ud = userData();
    const cfg = store.loadConfig(ud);
    const repos = new Set(cfg.repos);
    const phaseOf = (id) => { const c = conversations.get(id); return c ? { phase: c.phase, detail: c.detail } : null; };
    const sources = attention.conversationSources(
      store.listSessions(ud, '').filter((s) => repos.has(s.repo)), { runningIds: runningTurnIds(), phaseOf },
    );
    for (const repo of cfg.repos) {
      try {
        const names = Object.fromEntries(machineStore.list(repo).map((item) => [item.machine, item.name]));
        sources.push(...attention.taskSources(repo, runHistory.read(ud, repo), names));
      } catch { /* 定義や履歴が読めないリポジトリは飛ばす */ }
      try {
        const hostRoot = host.toHostPath(repo);
        const runs = agentFlow.listRuns(repo, 100, hostRoot).map((row) => (
          row.waiting ? agentFlow.readRun(repo, row.runId, hostRoot) : row
        ));
        sources.push(...attention.workflowSources(repo, runs));
      } catch { /* agent-flow の bus が無い・読めないリポジトリは飛ばす */ }
    }
    // 課題（agent-audit の洞察）。反証されたもの・会話へ渡したものは insightSources が落とす。
    sources.push(...attention.insightSources(audit.insights(ud, { limit: Infinity })).slice(0, 50));
    sources.push(...attention.batchSources(evaluation.readBatches(ud)));
    const seen = store.attentionBaseline(ud);
    return attention.project(sources, { seen: seen.items, since: seen.since });
  });
  handle('attention:seen', (p) => store.markAttentionSeen(userData(), String(p.key || ''), String(p.resultAt || '')));

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
    return c.scroll(p.lines, p.position);
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
  handle('shell:openVSCode', async (p) => {
    const target = dirsOf(p.repo, p.worktree, { mustExist: true }).fsDir;
    const url = new URL('vscode://file');
    url.pathname = target.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');
    try { await shell.openExternal(url.href); }
    catch { throw new Error('VS Codeを開けませんでした。VS Codeがインストールされているか確認してください。'); }
  });
  // 会話をテキストにして開く。開けなくてもファイルは書けているので、理由だけ返す
  handle('session:export', async (p) => {
    const ud = userData();
    const sess = store.readSession(ud, p.id);
    const out = sessionExport.write(ud, sess);
    const error = await shell.openPath(out.path);
    return { name: out.name, warning: error ? `書き出したファイルを開けませんでした: ${error}` : '' };
  });
  handle('shell:openFolder', (p) => shell.openPath(dirsOf(p.repo, p.worktree, { mustExist: true }).fsDir));
  handle('fs:existingArtifacts', (p) => files.existingArtifacts(dirsOf(requireRepo(p.repo), p.worktree, { mustExist: true }).fsDir, p.paths));
  handle('shell:openFile', async (p) => {
    const { target } = files.resolveInside(dirsOf(p.repo, p.worktree).fsDir, p.rel || '');
    const error = await shell.openPath(target);
    if (error) throw new Error(error);
    return true;
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
    for (const controller of selecting.values()) controller.abort();
    clearInterval(sweepTimer);
    clearInterval(shareTimer);
    updater.unschedule();
    auditor.unschedule();
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
