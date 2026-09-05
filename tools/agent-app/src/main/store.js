'use strict';

// userData の中だけを読み書きする。
//   config.json          … 登録したリポジトリと最後に選んだもの
//   sessions/<id>.json   … 会話 1 つ = 1 ファイル（リポジトリ・次のターンの CLI / モデル / モード・
//                          メッセージ列・CLI ごとのセッション ID）
//   attachments/<id>/    … 添付ファイル（attachments.js）
// リポジトリ側には何も置かない（CLI 自身が持つセッションログは CLI の管轄）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const settings = require('./settings');

// wslDistro    … Windows で、ドライブパス（C:\…）のリポジトリを扱う WSL ディストロ（'' なら既定）
// transport    … 'tmux'（対話起動。既定）| 'headless'（1 ターン 1 プロセス）
// useWorktree  … 会話ごとに git worktree で作業フォルダを分ける機能を使うか（既定 true）
// area         … 最後に開いていた主要領域（conversation | tasks | workflows）
// view         … 会話領域で最後に開いていた画面（chat | files）
// lastWorktree … リポジトリ → 最後に選んだ作業フォルダ名（'' はリポジトリ本体）
const DEFAULTS = {
  repos: [], lastRepo: '', lastCli: 'copilot', lastModel: '', lastReadonly: false,
  wslDistro: '', transport: 'tmux', useWorktree: true, area: 'conversation', view: 'chat', lastFiles: {}, lastWorktree: {},
  lastTask: {}, lastWorkflow: {},
  automationSkillDir: '', automationAgent: 'aider', automationModel: '',
};
const MAX_REPOS = 30;

function configPath(userData) { return path.join(userData, 'config.json'); }
function sessionsDir(userData) { return path.join(userData, 'sessions'); }

function normalize(raw) {
  const next = { ...DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) };
  next.repos = [...new Set((Array.isArray(next.repos) ? next.repos : []).map((r) => String(r || '')).filter(Boolean))].slice(0, MAX_REPOS);
  next.lastRepo = next.repos.includes(next.lastRepo) ? next.lastRepo : (next.repos[0] || '');
  next.lastCli = String(next.lastCli || DEFAULTS.lastCli);
  next.lastModel = String(next.lastModel || '');
  next.lastReadonly = Boolean(next.lastReadonly);
  next.wslDistro = String(next.wslDistro || '').trim();
  next.transport = next.transport === 'headless' ? 'headless' : 'tmux';
  next.useWorktree = next.useWorktree !== false;
  next.area = next.area === 'automation' ? 'tasks'
    : ['tasks', 'workflows'].includes(next.area) ? next.area : 'conversation';
  next.view = next.view === 'files' ? 'files' : 'chat';
  next.lastFiles = next.lastFiles && typeof next.lastFiles === 'object' ? next.lastFiles : {};
  next.lastWorktree = next.lastWorktree && typeof next.lastWorktree === 'object' ? next.lastWorktree : {};
  next.lastTask = next.lastTask && typeof next.lastTask === 'object' ? next.lastTask : {};
  next.lastWorkflow = next.lastWorkflow && typeof next.lastWorkflow === 'object' ? next.lastWorkflow : {};
  next.automationSkillDir = String(next.automationSkillDir || '').trim();
  next.automationAgent = String(next.automationAgent || 'aider').trim() || 'aider';
  next.automationModel = String(next.automationModel || '').trim();
  const userSettings = settings.normalize(next);
  const rawInstructions = next.instructions && typeof next.instructions === 'object' ? next.instructions : {};
  const rawExecution = next.execution && typeof next.execution === 'object' ? next.execution : {};
  const rawTiers = rawExecution.tiers && typeof rawExecution.tiers === 'object' ? rawExecution.tiers : {};
  next.instructions = { ...rawInstructions, ...userSettings.instructions };
  next.execution = {
    ...rawExecution,
    ...userSettings.execution,
    tiers: {
      ...rawTiers,
      ...Object.fromEntries(Object.entries(userSettings.execution.tiers).map(([tier, value]) => [
        tier,
        { ...(rawTiers[tier] && typeof rawTiers[tier] === 'object' ? rawTiers[tier] : {}), ...value },
      ])),
    },
  };
  return next;
}

function loadConfig(userData) {
  try { return normalize(JSON.parse(fs.readFileSync(configPath(userData), 'utf8'))); } catch { return normalize(null); }
}

function saveConfig(userData, patch) {
  const current = loadConfig(userData);
  const p = patch && typeof patch === 'object' ? patch : {};
  const executionPatch = p.execution && typeof p.execution === 'object' ? p.execution : null;
  const instructionsPatch = p.instructions && typeof p.instructions === 'object' ? p.instructions : null;
  const merged = { ...current, ...p };
  if (executionPatch) {
    const tierPatches = executionPatch.tiers && typeof executionPatch.tiers === 'object' ? executionPatch.tiers : {};
    const tiers = { ...current.execution.tiers };
    for (const [tier, value] of Object.entries(tierPatches)) {
      tiers[tier] = {
        ...(tiers[tier] && typeof tiers[tier] === 'object' ? tiers[tier] : {}),
        ...(value && typeof value === 'object' ? value : {}),
      };
    }
    merged.execution = {
      ...current.execution,
      ...executionPatch,
      tiers,
    };
  }
  if (instructionsPatch) merged.instructions = { ...current.instructions, ...instructionsPatch };
  const next = normalize(merged);
  fs.mkdirSync(userData, { recursive: true });
  const target = configPath(userData);
  const temp = `${target}.tmp-${process.pid}`;
  try {
    fs.writeFileSync(temp, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
    fs.renameSync(temp, target);
  } finally {
    try { fs.unlinkSync(temp); } catch { /* rename 済み、または未作成 */ }
  }
  return next;
}

function addRepo(userData, repo) {
  const dir = String(repo || '');
  if (!dir) throw new Error('フォルダを選んでください');
  const cfg = loadConfig(userData);
  return saveConfig(userData, { repos: [...cfg.repos.filter((r) => r !== dir), dir], lastRepo: dir });
}

function removeRepo(userData, repo) {
  const cfg = loadConfig(userData);
  const repos = cfg.repos.filter((r) => r !== String(repo || ''));
  return saveConfig(userData, { repos, lastRepo: cfg.lastRepo === repo ? (repos[0] || '') : cfg.lastRepo });
}

// 登録したリポジトリだけを触る。画面から届いたパスをそのまま信じない。
function isRegistered(userData, repo) {
  return loadConfig(userData).repos.includes(String(repo || ''));
}

function sessionPath(userData, id) {
  if (!/^[0-9a-f-]{36}$/.test(String(id || ''))) throw new Error(`セッション ID が不正です: ${id}`);
  return path.join(sessionsDir(userData), `${id}.json`);
}

// 会話の形を揃える。
//   cli / model / readonly … **次のターン**の既定（ターンごとに変えられる。最後に使ったもの）
//   cliSessions            … CLI 名 → { id, seen }。id は CLI 側のセッション ID（'' なら再開手段なし）、
//                            seen はその CLI の文脈に入っているメッセージ数（別の CLI で進めた分は
//                            次にその CLI へ戻るときに追いつかせる）
//   live                   … tmux で今動いている CLI の起動条件 { cli, model, readonly }（無ければ null）
// 以前の形（cliSession 1 つ）はここで cliSessions へ写す。
function normalizeSession(sess) {
  if (!sess.cliSessions || typeof sess.cliSessions !== 'object') sess.cliSessions = {};
  if (sess.cliSession && !sess.cliSessions[sess.cli]) {
    sess.cliSessions[sess.cli] = { id: String(sess.cliSession), seen: (sess.messages || []).length };
  }
  delete sess.cliSession;
  if (!sess.live || typeof sess.live !== 'object') sess.live = null;
  sess.policy = ['recommended', 'saving', 'quality', 'direct'].includes(sess.policy) ? sess.policy : 'direct';
  sess.tier = ['small', 'medium', 'large'].includes(sess.tier) ? sess.tier : '';
  return sess;
}

function readSession(userData, id) {
  return normalizeSession(JSON.parse(fs.readFileSync(sessionPath(userData, id), 'utf8')));
}

// その CLI のセッション情報（無ければ null）
function cliEntry(sess, cli) {
  const e = sess.cliSessions && sess.cliSessions[String(cli || '')];
  if (!e || typeof e !== 'object') return null;
  const entry = { id: String(e.id || ''), seen: Number(e.seen) || 0 };
  if ('setupApplied' in e) entry.setupApplied = Boolean(e.setupApplied);
  return entry;
}

function setCliEntry(userData, id, cli, patch) {
  const sess = readSession(userData, id);
  const cur = cliEntry(sess, cli) || { id: '', seen: 0 };
  sess.cliSessions[String(cli)] = { ...cur, ...(patch || {}) };
  return writeSession(userData, sess);
}

function writeSession(userData, sess) {
  fs.mkdirSync(sessionsDir(userData), { recursive: true });
  sess.updatedAt = new Date().toISOString();
  fs.writeFileSync(sessionPath(userData, sess.id), `${JSON.stringify(sess, null, 2)}\n`, 'utf8');
  return sess;
}

// worktree … 作業フォルダの名前（'' はリポジトリ本体）。作ったあとは変えない——
// tmux セッションの cwd も CLI 側の文脈もそこで始まっているため。
function createSession(userData, { repo, cli, model = '', readonly = false, policy = 'direct', tier = '', transport = 'tmux', worktree = '', branch = '' }) {
  if (!repo) throw new Error('リポジトリを選んでください');
  if (!cli) throw new Error('エージェントを選んでください');
  const now = new Date().toISOString();
  return writeSession(userData, {
    id: crypto.randomUUID(), repo: String(repo), cli: String(cli), model: String(model || ''),
    readonly: Boolean(readonly), policy: String(policy || 'direct'), tier: String(tier || ''),
    transport: transport === 'headless' ? 'headless' : 'tmux',
    worktree: String(worktree || ''), branch: String(branch || ''),
    title: '', cliSessions: {}, live: null, messages: [], createdAt: now, updatedAt: now,
  });
}

// 会話をぜんぶ読む（添付の掃除など、中身が要るとき）
function readAllSessions(userData) {
  let names;
  try { names = fs.readdirSync(sessionsDir(userData)); } catch { return []; }
  const out = [];
  for (const f of names) {
    if (!f.endsWith('.json')) continue;
    try { out.push(readSession(userData, f.slice(0, -5))); } catch { /* 壊れたファイルは飛ばす */ }
  }
  return out;
}

function listSessions(userData, repo) {
  let names;
  try { names = fs.readdirSync(sessionsDir(userData)); } catch { return []; }
  const out = [];
  for (const f of names) {
    if (!f.endsWith('.json')) continue;
    try {
      const s = JSON.parse(fs.readFileSync(path.join(sessionsDir(userData), f), 'utf8'));
      if (repo && s.repo !== repo) continue;
      out.push({
        id: s.id, repo: s.repo, cli: s.cli, model: s.model, readonly: s.readonly,
        policy: s.policy || 'direct', tier: s.tier || '',
        transport: s.transport || 'headless', worktree: s.worktree || '', branch: s.branch || '',
        title: s.title, updatedAt: s.updatedAt, count: (s.messages || []).length,
      });
    } catch { /* 壊れたファイルは一覧に出さない */ }
  }
  return out.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
}

function updateSession(userData, id, patch) {
  const sess = readSession(userData, id);
  const allowed = ['title', 'cli', 'model', 'readonly', 'policy', 'tier', 'transport', 'live'];
  for (const k of allowed) if (patch && k in patch) sess[k] = patch[k];
  if (patch && 'cli' in patch) sess.cli = String(sess.cli || '');
  if (patch && 'model' in patch) sess.model = String(sess.model || '');
  if (patch && 'readonly' in patch) sess.readonly = Boolean(sess.readonly);
  if (patch && 'policy' in patch) sess.policy = ['recommended', 'saving', 'quality', 'direct'].includes(sess.policy) ? sess.policy : 'direct';
  if (patch && 'tier' in patch) sess.tier = ['small', 'medium', 'large'].includes(sess.tier) ? sess.tier : '';
  if (patch && 'transport' in patch) sess.transport = sess.transport === 'headless' ? 'headless' : 'tmux';
  if (patch && 'live' in patch) sess.live = sess.live && typeof sess.live === 'object' ? sess.live : null;
  return writeSession(userData, sess);
}

function appendMessage(userData, id, message) {
  const sess = readSession(userData, id);
  sess.messages.push({ at: new Date().toISOString(), ...message });
  if (!sess.title && message.role === 'user') sess.title = String(message.text || '').split('\n')[0].slice(0, 60);
  return writeSession(userData, sess);
}

function removeSession(userData, id) {
  try { fs.unlinkSync(sessionPath(userData, id)); } catch { /* 無ければ無いでよい */ }
  return true;
}

module.exports = {
  DEFAULTS, loadConfig, saveConfig, addRepo, removeRepo, isRegistered,
  createSession, readSession, listSessions, updateSession, appendMessage, removeSession,
  normalizeSession, cliEntry, setCliEntry, sessionsDir, readAllSessions,
};
