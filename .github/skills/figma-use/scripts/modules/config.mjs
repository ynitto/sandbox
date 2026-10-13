// 認証情報の解決。他スキルの config_loader.py と同じ規則で connections.yaml を探す。
//
//   1. $SKILL_CONNECTIONS_FILE（指定時はこのファイルだけ）
//   2. ワークスペース: cwd から親へたどって見つけた {agent_dir}/skills/ の隣の connections.yaml
//   3. グローバル: このスクリプトの置き場から 4 階層上（scripts/ -> figma-use/ -> skills/ -> agent_dir/）
//   4. 環境変数 FIGMA_TOKEN
//
// connections.yaml の `figma:` だけを読む。依存を持たないため YAML は
// 「サービス名: の下にラベル付きの平らな辞書の配列」という connections.yaml の形だけを解釈する。
// トークンの値はこのモジュールの外へ「あるかどうか」と「どこから来たか」しか出さない。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// scripts/modules/ -> scripts/ -> figma-use/ -> skills/
const FILE_SKILL_HOME = path.resolve(HERE, '..', '..', '..');
const AGENT_WORKSPACE_DIR = { '.copilot': '.github' };

export const DEFAULT_API_BASE = 'https://api.figma.com';

function cwdSkillHome(cwd = process.cwd(), fileSkillHome = FILE_SKILL_HOME) {
  const skillsDirName = path.basename(fileSkillHome);
  const agentDirName = path.basename(path.dirname(fileSkillHome));
  const wsAgentDir = AGENT_WORKSPACE_DIR[agentDirName] || agentDirName;
  let dir = path.resolve(cwd);
  for (;;) {
    const candidate = wsAgentDir.startsWith('.')
      ? path.join(dir, wsAgentDir, skillsDirName)
      : path.join(dir, skillsDirName);
    if (isDir(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

export function findConnectionFiles({ env = process.env, cwd = process.cwd(), fileSkillHome = FILE_SKILL_HOME } = {}) {
  const explicit = env.SKILL_CONNECTIONS_FILE;
  if (explicit) return fs.existsSync(explicit) ? [path.resolve(explicit)] : [];
  const found = [];
  const ws = cwdSkillHome(cwd, fileSkillHome);
  if (ws) {
    const p = path.join(path.dirname(ws), 'connections.yaml');
    if (fs.existsSync(p)) found.push(p);
  }
  const global = path.join(path.dirname(fileSkillHome), 'connections.yaml');
  if (fs.existsSync(global) && !found.includes(global)) found.push(global);
  return found;
}

function unquote(value) {
  const v = value.trim();
  if (v.length >= 2 && ((v[0] === '"' && v.at(-1) === '"') || (v[0] === "'" && v.at(-1) === "'"))) {
    return v.slice(1, -1);
  }
  // 行末コメント（空白 + #）を落とす。
  const hash = v.search(/\s#/);
  return hash >= 0 ? v.slice(0, hash).trim() : v;
}

/** connections.yaml から指定サービスのエントリ（平らな辞書の配列）を取り出す。 */
export function parseServiceEntries(text, service) {
  const entries = [];
  let inService = false;
  let current = null;
  let itemIndent = -1;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/\t/g, '  ');
    if (!line.trim() || line.trimStart().startsWith('#')) continue;
    const indent = line.length - line.trimStart().length;
    if (indent === 0) {
      const m = line.match(/^([^:#]+):\s*(.*)$/);
      inService = Boolean(m && m[1].trim() === service);
      current = null;
      itemIndent = -1;
      continue;
    }
    if (!inService) continue;
    const body = line.trim();
    if (body.startsWith('- ') || body === '-') {
      current = {};
      entries.push(current);
      itemIndent = indent;
      const rest = body.slice(1).trim();
      const m = rest.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
      if (m) current[m[1]] = unquote(m[2]);
      continue;
    }
    if (current && indent > itemIndent) {
      const m = body.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
      if (m) current[m[1]] = unquote(m[2]);
    }
  }
  return entries;
}

function expandEnv(value, env) {
  return value.replace(/\$\{([^}]+)\}/g, (all, name) => (env[name] !== undefined ? env[name] : all));
}

const UNEXPANDED_RE = /\$\{[^}]+\}/;

/**
 * トークンを解決する。戻り値の token は API 呼び出しにだけ使い、表示しない。
 * source は表示してよい説明（ファイルのパスとラベル、または環境変数名）。
 */
export function resolveCredentials({ label = 'default', env = process.env, cwd = process.cwd(), fileSkillHome = FILE_SKILL_HOME } = {}) {
  const files = findConnectionFiles({ env, cwd, fileSkillHome });
  const notes = [];
  if (env.SKILL_CONNECTIONS_FILE && files.length === 0) {
    notes.push(`SKILL_CONNECTIONS_FILE が指すファイルがありません: ${env.SKILL_CONNECTIONS_FILE}`);
  }
  let apiBase = env.FIGMA_API_BASE || DEFAULT_API_BASE;
  for (const file of files) {
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch (e) {
      notes.push(`読めません: ${file}（${e.code || e.message}）`);
      continue;
    }
    const entry = parseServiceEntries(text, 'figma').find(
      (e) => (e.label || 'default').toLowerCase() === label.toLowerCase(),
    );
    if (!entry) continue;
    if (entry.api_base) apiBase = expandEnv(entry.api_base, env);
    const token = entry.token ? expandEnv(entry.token, env) : '';
    if (token && !UNEXPANDED_RE.test(token)) {
      return { token, source: `connections.yaml（${file} / label: ${label}）`, files, apiBase, notes };
    }
    notes.push(
      token
        ? `${file} の figma（label: ${label}）の token が参照する環境変数が未設定です`
        : `${file} の figma（label: ${label}）に token がありません`,
    );
  }
  if (env.FIGMA_TOKEN) return { token: env.FIGMA_TOKEN, source: '環境変数 FIGMA_TOKEN', files, apiBase, notes };
  return { token: null, source: null, files, apiBase, notes };
}

/** 書き込み先の候補（doctor の案内用）。ファイルが無くてもよい。 */
export function connectionsWritePath({ cwd = process.cwd(), fileSkillHome = FILE_SKILL_HOME } = {}) {
  const ws = cwdSkillHome(cwd, fileSkillHome);
  return path.join(path.dirname(ws || fileSkillHome), 'connections.yaml');
}
