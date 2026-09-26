'use strict';

// agent-project の状態フォルダ（旧 --root）を、agent-app のプロジェクトへ取り込む。
//
//   charter.md の ## repos / repos.{yaml,yml,json} … project.yaml の repos（owns あり → 作業、無し → 参照、
//                                                   最初の作業 → 主。同じ URL の複数エントリは 1 つにまとめる）
//   host.yaml の repos[].local                    … この PC のフォルダ（repoPaths）
//   rules.md / decisions/ / notes/ / charter.md   … ナレッジリポジトリの projects/<フォルダ>/ へ写す
//   backlog/ needs/ archive/ など                 … 移さない（件数だけ返す。旧フォルダは消さない）
//
// 読むのも書くのもファイルだけ。git のコミットは呼び出し側（ipc / scripts）が決める。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const projects = require('./projects');

const LEFT_BEHIND = ['backlog', 'needs', 'archive', 'inbox', 'commands', 'journal-archive'];
const KEY_ALIAS = { 説明: 'desc', ベース: 'base', ターゲット: 'target', パス: 'path', 担当: 'owns' };

function readText(file) { try { return fs.readFileSync(file, 'utf8'); } catch { return ''; } }

// charter.md の `## repos`。見出しは `- name = url` か `- url`、インデントした `- key: value` がメタ。
function charterRepos(charter) {
  const out = {};
  let inRepos = false;
  let current = null;
  let inComment = false;
  for (const line of String(charter || '').split(/\r?\n/)) {
    if (/<!--/.test(line) && !/-->/.test(line)) { inComment = true; continue; }
    if (inComment) { if (/-->/.test(line)) inComment = false; continue; }
    if (/^##\s/.test(line)) { inRepos = /^##\s+repos\s*$/i.test(line); current = null; continue; }
    if (!inRepos || /^\s*#/.test(line)) continue;
    const head = /^-\s+(?:([^=\s]+)\s*=\s*)?(\S+)\s*(?:#.*)?$/.exec(line);
    if (head) {
      const url = head[2];
      const name = head[1] || projects.repoLabel(url);
      current = { url };
      out[name] = current;
      continue;
    }
    const meta = /^\s+-\s+([^:：]+)[:：]\s*(.*?)\s*(?:#.*)?$/.exec(line);
    if (meta && current) {
      const key = KEY_ALIAS[meta[1].trim()] || meta[1].trim();
      current[key] = meta[2];
    }
  }
  return out;
}

function registry(root) {
  for (const name of ['repos.yaml', 'repos.yml', 'repos.json']) {
    const raw = readText(path.join(root, name));
    if (!raw) continue;
    const data = name.endsWith('.json') ? JSON.parse(raw) : YAML.parse(raw);
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      const out = {};
      for (const [key, value] of Object.entries(data)) if (!key.startsWith('_') && value && typeof value === 'object') out[key] = value;
      return { source: name, repos: out };
    }
  }
  return { source: 'charter.md', repos: charterRepos(readText(path.join(root, 'charter.md'))) };
}

// 同じ URL のエントリ（モノレポのフォルダ別・ブランチ別）は 1 つにまとめる
function toProjectRepos(entries) {
  const byUrl = new Map();
  for (const [name, entry] of Object.entries(entries)) {
    const url = String(entry.url || '').trim();
    if (!url) continue;
    const key = projects.normalizeUrl(url);
    const owns = entry.readonly === true || entry.readonly === 'true' ? [] : (Array.isArray(entry.owns) ? entry.owns : String(entry.owns || '').split(/[,\s]+/)).filter(Boolean);
    const prev = byUrl.get(key) || { url, owns: [], descs: [], names: [] };
    prev.owns.push(...owns);
    if (entry.desc) prev.descs.push(String(entry.desc));
    prev.names.push(name);
    byUrl.set(key, prev);
  }
  return [...byUrl.values()].map((item) => ({
    url: item.url,
    role: item.owns.length ? 'work' : 'reference',
    desc: [...new Set(item.descs)].join(' / '),
    ...(item.owns.length ? { owns: [...new Set(item.owns)] } : {}),
  }));
}

function hostRepoPaths(hostYaml) {
  const raw = readText(hostYaml);
  if (!raw) return {};
  let data;
  try { data = YAML.parse(raw); } catch { return {}; }
  const out = {};
  for (const item of Array.isArray(data && data.repos) ? data.repos : []) {
    if (item && item.url && item.local) out[projects.normalizeUrl(item.url)] = String(item.local);
  }
  return out;
}

function mdFiles(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }).filter((d) => d.isFile() && d.name.endsWith('.md')).map((d) => d.name).sort(); }
  catch { return []; }
}

function count(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }).filter((d) => d.isFile() && d.name.endsWith('.md')).length; }
  catch { return 0; }
}

// 何をどこへ写すかを決める（まだ何も書かない）。
//   root     … agent-project の状態フォルダ
//   hostYaml … ~/.agents/agent-project.host.yaml（省略可）
//   name     … プロジェクト名（省略時は charter の最初の見出し、無ければフォルダ名）
function plan({ root, hostYaml = '', name = '' }) {
  const dir = path.resolve(String(root || ''));
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) throw new Error(`フォルダが見つかりません: ${dir}`);
  const charter = readText(path.join(dir, 'charter.md'));
  const reg = registry(dir);
  const title = (/^#\s+(.+)$/m.exec(charter.replace(/<!--[\s\S]*?-->/g, '')) || [])[1] || '';
  const project = projects.normalize({ name: name || title.trim() || path.basename(dir), repos: toProjectRepos(reg.repos) });
  if (!project.repos.length && !charter && !fs.existsSync(path.join(dir, 'rules.md'))) {
    throw new Error('agent-project の状態フォルダではないようです（charter.md・repos・rules.md のどれも無い）');
  }
  const copies = [];
  if (charter) copies.push({ from: 'charter.md', to: 'charter.md' });
  if (fs.existsSync(path.join(dir, 'rules.md'))) copies.push({ from: 'rules.md', to: 'rules.md' });
  for (const sub of ['decisions', 'notes']) {
    for (const file of mdFiles(path.join(dir, sub))) copies.push({ from: `${sub}/${file}`, to: `${sub}/${file}` });
  }
  const leftBehind = {};
  for (const sub of LEFT_BEHIND) { const n = count(path.join(dir, sub)); if (n) leftBehind[sub] = n; }
  const repoPaths = hostYaml ? hostRepoPaths(hostYaml) : {};
  return { root: dir, source: reg.source, project, folder: projects.folderName(project.name), copies, leftBehind, repoPaths };
}

// ナレッジリポジトリへ書く。既にあるファイルは上書きしない（skipped に並べる）。
// 書いたファイル（kb からの相対）を返す。
function apply(kb, planned) {
  const base = path.join(kb, projects.DIR, planned.folder);
  if (fs.existsSync(projects.projectFile(kb, planned.folder))) {
    throw new Error(`同じ名前のプロジェクトが既にあります: ${projects.DIR}/${planned.folder}`);
  }
  const written = [];
  const skipped = [];
  for (const item of planned.copies) {
    const target = path.join(base, item.to);
    if (fs.existsSync(target)) { skipped.push(item.to); continue; }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(planned.root, item.from), target);
    written.push(path.posix.join(projects.DIR, planned.folder, item.to));
  }
  const readme = path.join(base, 'README.md');
  if (!fs.existsSync(readme)) {
    const links = planned.copies.map((item) => `- [${item.to}](${item.to})`);
    fs.writeFileSync(readme, `# ${planned.project.name}\n\n## 索引\n\n${links.join('\n')}${links.length ? '\n' : ''}`, 'utf8');
  }
  written.push(...projects.write(kb, planned.folder, planned.project));
  return { written: [...new Set(written)], skipped };
}

module.exports = { charterRepos, registry, toProjectRepos, hostRepoPaths, plan, apply, LEFT_BEHIND };
