'use strict';

// この PC の `~/.agents/agent-project.host.yaml` から `repos[]`（ノード固有のローカル
// クローン宣言・S3）を読む。Python 側 `agentcore.repolocal` と同じ契約の JS 実装。
//
// **なぜ JS で読み直すか**: この宣言を使うのは UI の即応な部分（CLIチャットの起動先候補）で、
// 候補を出すたびに Python を起動すると一覧の描画がプロセス起動待ちになる。契約（キーの形と
// URL 正規化の規則）は agentcore 側が正で、ここはその読み手。
//
// **YAML の扱い**: このアプリは YAML パーサを持たない（`toolconfig.parseFlatYaml` も
// トップレベルのスカラだけ）。`repos:` は決まった形——`- url:` で始まり同じインデントの
// `local:` が続くマップの列——なので、その形だけを読む小さなパーサを置く。ネストした
// マッピングやフロー記法（`repos: [{...}]`）は読まない: 読めなければ「宣言なし」に倒れ、
// 呼び出し側はミラー取得へ落ちるだけで動作は正しい（速度が出ないだけ）。
// host.yaml を JSON（`agent-project.host.json`）で書いている場合は素直に全部読める。

const fs = require('fs');
const os = require('os');
const path = require('path');

const HOST_CONFIG_NAMES = [
  'agent-project.host.yaml',
  'agent-project.host.yml',
  'agent-project.host.json',
];

function agentsHome() {
  const override = process.env.AGENT_PROJECT_AGENTS_HOME;
  return override ? String(override) : path.join(os.homedir(), '.agents');
}

function findHostConfig() {
  for (const base of [process.cwd(), agentsHome()]) {
    for (const name of HOST_CONFIG_NAMES) {
      const p = path.join(base, name);
      try {
        if (fs.statSync(p).isFile()) return p;
      } catch { /* not present */ }
    }
  }
  return null;
}

// git URL/パスの正規形（`agentcore.repolocal.normalize_repo_url` と同じ規則）。
// 末尾の .git とスラッシュ、大小文字を吸収し、ローカルパス表記だけ絶対化する。
// scp 形式（user@host:path）をパスとして絶対化すると cwd 配下に化けるので URL 扱いにする。
function normalizeRepoUrl(url) {
  let s = String(url || '').trim().replace(/\/+$/, '');
  if (s.endsWith('.git')) s = s.slice(0, -4);
  if (!s) return '';
  if (!s.includes('://') && !/^[^/\\]+@[^/\\]+:/.test(s)) {
    const expanded = s.replace(/^~(?=$|\/|\\)/, os.homedir());
    try {
      return path.resolve(expanded).toLowerCase();
    } catch {
      return expanded.toLowerCase();
    }
  }
  return s.toLowerCase();
}

function sameRepo(a, b) {
  const na = normalizeRepoUrl(a);
  return !!na && na === normalizeRepoUrl(b);
}

// `repos:` ブロックだけを読む（上のコメントの制限つき）。
function parseHostRepos(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let inBlock = false;
  let itemIndent = -1;
  let cur = null;
  const flush = () => { if (cur && cur.url) out.push(cur); cur = null; };
  for (const line of lines) {
    if (!inBlock) {
      if (/^repos:\s*$/.test(line)) inBlock = true;
      continue;
    }
    if (!line.trim() || line.trim().startsWith('#')) continue;
    const indent = line.length - line.replace(/^\s*/, '').length;
    if (indent === 0) break;                       // 次のトップレベルキー → ブロック終わり
    const body = line.trim();
    const item = body.match(/^-\s*(.*)$/);
    if (item) {
      flush();
      if (itemIndent < 0) itemIndent = indent;
      cur = {};
      if (item[1]) {
        const kv = item[1].match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
        if (kv) cur[kv[1]] = stripQuotes(kv[2]);
      }
      continue;
    }
    if (!cur || indent <= itemIndent) continue;    // 項目の外（別ブロック）は読まない
    const kv = body.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
    if (kv) cur[kv[1]] = stripQuotes(kv[2]);
  }
  flush();
  return out;
}

function stripQuotes(s) {
  const t = String(s || '').trim().replace(/\s+#.*$/, '');
  if (t.length >= 2 && ((t[0] === '"' && t.endsWith('"')) || (t[0] === "'" && t.endsWith("'")))) {
    return t.slice(1, -1);
  }
  return t;
}

// このノードの repos 宣言（[{url, local}, …]）。読めなければ空配列。
function loadNodeRepos() {
  const file = findHostConfig();
  if (!file) return [];
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return [];
  }
  if (file.toLowerCase().endsWith('.json')) {
    try {
      const data = JSON.parse(text);
      const raw = data && data.repos;
      if (Array.isArray(raw)) return raw.filter((e) => e && e.url);
      if (raw && typeof raw === 'object') {
        return Object.entries(raw).map(([url, v]) =>
          (v && typeof v === 'object') ? { url, ...v } : { url, local: String(v || '') });
      }
      return [];
    } catch {
      return [];
    }
  }
  return parseHostRepos(text);
}

// URL に対応する、このノードのローカルクローン（実在するもの）。無ければ ''。
function resolveLocalRepo(url, repos) {
  if (!String(url || '').trim()) return '';
  for (const e of (repos || loadNodeRepos())) {
    const local = String((e && e.local) || '').trim();
    if (!local || !sameRepo(e.url, url)) continue;
    const expanded = local.replace(/^~(?=$|\/|\\)/, os.homedir());
    try {
      if (fs.statSync(expanded).isDirectory()) return expanded;
    } catch { /* 宣言はあるが実体が無い */ }
  }
  return '';
}

module.exports = {
  HOST_CONFIG_NAMES,
  findHostConfig,
  normalizeRepoUrl,
  sameRepo,
  parseHostRepos,
  loadNodeRepos,
  resolveLocalRepo,
};
