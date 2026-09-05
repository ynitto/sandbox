'use strict';

// userData の中だけを読み書きする。
//   config.json          … 登録したリポジトリと最後に選んだもの
//   sessions/<id>.json   … 会話 1 つ = 1 ファイル（リポジトリ・CLI・モード・メッセージ列・CLI 側のセッション ID）
// リポジトリ側には何も置かない（CLI 自身が持つセッションログは CLI の管轄）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// wslDistro    … Windows で、ドライブパス（C:\…）のリポジトリを扱う WSL ディストロ（'' なら既定）
// transport    … 'tmux'（対話起動。既定）| 'headless'（1 ターン 1 プロセス）
// view         … 最後に開いていた画面（chat | files）
// lastWorktree … リポジトリ → 最後に選んだ作業フォルダ名（'' はリポジトリ本体）
const DEFAULTS = {
  repos: [], lastRepo: '', lastCli: 'copilot', lastModel: '', lastReadonly: false,
  wslDistro: '', transport: 'tmux', view: 'chat', lastFiles: {}, lastWorktree: {},
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
  next.view = next.view === 'files' ? 'files' : 'chat';
  next.lastFiles = next.lastFiles && typeof next.lastFiles === 'object' ? next.lastFiles : {};
  next.lastWorktree = next.lastWorktree && typeof next.lastWorktree === 'object' ? next.lastWorktree : {};
  return next;
}

function loadConfig(userData) {
  try { return normalize(JSON.parse(fs.readFileSync(configPath(userData), 'utf8'))); } catch { return normalize(null); }
}

function saveConfig(userData, patch) {
  const next = normalize({ ...loadConfig(userData), ...(patch || {}) });
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(configPath(userData), `${JSON.stringify(next, null, 2)}\n`, 'utf8');
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

function readSession(userData, id) {
  return JSON.parse(fs.readFileSync(sessionPath(userData, id), 'utf8'));
}

function writeSession(userData, sess) {
  fs.mkdirSync(sessionsDir(userData), { recursive: true });
  sess.updatedAt = new Date().toISOString();
  fs.writeFileSync(sessionPath(userData, sess.id), `${JSON.stringify(sess, null, 2)}\n`, 'utf8');
  return sess;
}

// worktree … 作業フォルダの名前（'' はリポジトリ本体）。作ったあとは変えない——
// tmux セッションの cwd も CLI 側の文脈もそこで始まっているため。
function createSession(userData, { repo, cli, model = '', readonly = false, transport = 'tmux', worktree = '', branch = '' }) {
  if (!repo) throw new Error('リポジトリを選んでください');
  if (!cli) throw new Error('エージェントを選んでください');
  const now = new Date().toISOString();
  return writeSession(userData, {
    id: crypto.randomUUID(), repo: String(repo), cli: String(cli), model: String(model || ''),
    readonly: Boolean(readonly), transport: transport === 'headless' ? 'headless' : 'tmux',
    worktree: String(worktree || ''), branch: String(branch || ''),
    title: '', cliSession: '', messages: [], createdAt: now, updatedAt: now,
  });
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
        transport: s.transport || 'headless', worktree: s.worktree || '', branch: s.branch || '',
        title: s.title, updatedAt: s.updatedAt, count: (s.messages || []).length,
      });
    } catch { /* 壊れたファイルは一覧に出さない */ }
  }
  return out.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
}

function updateSession(userData, id, patch) {
  const sess = readSession(userData, id);
  const allowed = ['title', 'model', 'readonly', 'cliSession', 'transport'];
  for (const k of allowed) if (patch && k in patch) sess[k] = patch[k];
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
};
