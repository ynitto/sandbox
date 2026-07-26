'use strict';

// この PC の `~/.agents/agent-project.host.yaml` から `repos[]`（ノード固有のローカル
// クローン宣言・S3）を読む。Python 側 `agentcore.repolocal` と同じ契約の JS 実装。
//
// **なぜ JS で読み直すか**: この宣言を使うのは UI の即応な部分（CLIチャットの起動先候補）で、
// 候補を出すたびに Python を起動すると一覧の描画がプロセス起動待ちになる。契約（キーの形と
// URL 正規化の規則）は agentcore 側が正で、ここはその読み手。
//
// **YAML の扱い**: 読みは base/main/yaml.js（YAML ライブラリ）へ寄せている。以前は
// 「`- url:` で始まり同じインデントの `local:` が続く」形だけを読む小さなパーサだったが、
// `repos:  # このPCのクローン` のようなインラインコメントで `repos:` 行の照合が外れ、
// 宣言が丸ごと無いことにされていた。壊れ方が「静かに宣言なしへ倒れる」＝速度が出ない理由が
// 分からない形なので、読みは本物のパーサに任せる。
// 壊れた YAML は従来どおり「宣言なし」に倒れる（呼び出し側はミラー取得へ落ちるだけ）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');

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
      // Python 側は `Path.resolve()`＝**symlink も解決する**。ここが `path.resolve` だけだと、
      // 同じクローンを symlink 経由で宣言した PC で「同じリポジトリ」と読めない——症状は
      // 「なぜかローカルクローンが使われない（＝速度が出ない）」で、理由が画面から分からない。
      return fs.realpathSync.native(expanded).toLowerCase();
    } catch {
      // 実体が無いパスは解決できない。Python の `resolve(strict=False)` と同じく、
      // 絶対化だけして返す。
      try {
        return path.resolve(expanded).toLowerCase();
      } catch {
        return expanded.toLowerCase();
      }
    }
  }
  return s.toLowerCase();
}

function sameRepo(a, b) {
  const na = normalizeRepoUrl(a);
  return !!na && na === normalizeRepoUrl(b);
}

// repos 宣言を [{url, local, …}] へ正規化する。列（`- url: …`）とマップ
// （`<url>: {local: …}` / `<url>: <local>`）の両方を受ける（JSON 側と同じ規則）。
function normalizeReposDecl(raw) {
  if (Array.isArray(raw)) {
    return raw
      .filter((e) => isPlainObject(e) && scalarString(e.url))
      .map((e) => ({ ...e, url: scalarString(e.url) }));
  }
  if (!isPlainObject(raw)) return [];
  return Object.entries(raw).map(([url, v]) => (
    isPlainObject(v) ? { url, ...v } : { url, local: scalarString(v) || '' }
  ));
}

// host.yaml の `repos:` を読む。読めなければ空配列（＝宣言なし）。
function parseHostRepos(text) {
  const doc = parseYaml(text);
  return isPlainObject(doc) ? normalizeReposDecl(doc.repos) : [];
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
      return normalizeReposDecl(data && data.repos);
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
