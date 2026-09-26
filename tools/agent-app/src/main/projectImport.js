'use strict';

// agent-project の再利用できる資料を Markdown に変換・統合する。元の資料は変更しない。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const projects = require('./projects');

const crypto = require('crypto');
const bundle = require('../shared/projectImportBundle');
const IMPORT_GROUPS = [
  { id: 'policy', paths: ['charter.md', 'rules.md'] },
  { id: 'knowledge', paths: ['decisions', 'notes'] },
  { id: 'outcomes', paths: ['archive', 'DELIVERY.md'] },
  { id: 'pending', paths: ['backlog', 'backlog.md', 'needs', 'inbox'] },
];
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

// 管理用メタデータとコメントを除く。本文・コード・検証の成否は原文のまま保つ。
function extract(text, group, source) {
  let section = '';
  let fence = '';
  let comment = false;
  const lines = text.split(/\r?\n/);
  const kept = [];
  const headings = { goal: '目的', constraints: '制約', assumptions: '前提', deliverables: '成果物', acceptance: '受入基準', links: '参考資料' };
  const fields = { why: '背景', desc: '内容', scope: '対象範囲', out_of_scope: '対象外', constraints: '制約', hints: '補足', risks: 'リスク', demo: '確認方法', accept: '受入基準', acceptance: '受入基準', task_acceptance_criteria: '受入基準', verify: '検証', verification_commands: '検証コマンド' };
  for (let line of lines) {
    if (!fence) {
      if (comment) {
        const end = line.indexOf('-->');
        if (end < 0) continue;
        line = line.slice(end + 3); comment = false;
      }
      line = line.replace(/<!--[\s\S]*?-->/g, '');
      const start = line.indexOf('<!--');
      if (start >= 0) { line = line.slice(0, start); comment = true; }
      const heading = /^##\s+(.+)/.exec(line);
      if (heading) section = heading[1].trim().toLowerCase();
      if (source === 'charter.md' && section === 'repos') continue;
    }
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (fence[0] === marker[1][0] && marker[1].length >= fence.length) fence = '';
      kept.push(line); continue;
    }
    if (fence) { kept.push(line); continue; }
    if (['outcomes', 'pending'].includes(group) && /^\s*-\s*(status|source|priority|retries|review|level|track|claimed_by|lease|started_at|updated_at|attempts|after|workspace|routed_by|node|cohort_items|cohort|cohort_role|read_allocation)\s*[:：]/i.test(line)) continue;
    if (source === 'charter.md') line = line.replace(/^##\s+(\w+)\s*$/, (all, key) => headings[key.toLowerCase()] ? `## ${headings[key.toLowerCase()]}` : all);
    if (['outcomes', 'pending'].includes(group)) line = line.replace(/^-\s*(\w+)\s*[:：]\s*(.*)$/, (all, key, value) => fields[key] ? (value.trim() ? `- ${fields[key]}: ${value.replace(/\s*⏎\s*/g, '\n  ')}` : '') : all);
    // 相対リンクの参照先はコピーしない。壊れたリンクにせず元資料の参照として残す。
    kept.push(line.replace(/(!?)\[([^\]]*)\]\(([^)]+)\)/g, (all, image, label, target) =>
      /^(?:https?:|mailto:|#)/i.test(target) ? all : `${label}（元資料: ${target}）`));
  }
  return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

// backlog.md の複数タスクも、選択・実行の単位を1件ずつに保つ。
function pendingEntries(text) {
  const lines = text.split(/\r?\n/), starts = [];
  let fence = '', comment = false;
  lines.forEach((line, index) => {
    if (!fence && /<!--/.test(line)) comment = true;
    if (comment) { if (/-->/.test(line)) comment = false; return; }
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (marker) { if (!fence) fence = marker[1]; else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = ''; return; }
    if (!fence && /^##\s+[^\s:：]+[:：]\s*\S/.test(line)) starts.push(index);
  });
  if (starts.length < 2) return [{ text, suffix: '' }];
  const preface = lines.slice(0, starts[0]).join('\n').replace(/<!--[\s\S]*?-->/g, '').replace(/^#.*$/gm, '').trim();
  return starts.map((start, index) => ({
    text: lines.slice(start, starts[index + 1] ?? lines.length).join('\n') + (preface ? `\n\n### 共通事項\n${preface}` : ''),
    suffix: `#task-${index + 1}`,
  }));
}

function plan({ root, hostYaml = '', name = '' }) {
  const dir = path.resolve(String(root || ''));
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) throw new Error(`フォルダが見つかりません: ${dir}`);
  const charter = readText(path.join(dir, 'charter.md'));
  const reg = registry(dir);
  const title = (/^#\s+(.+)$/m.exec(charter.replace(/<!--[\s\S]*?-->/g, '')) || [])[1] || '';
  const project = projects.normalize({ name: name || title.trim() || path.basename(dir), repos: toProjectRepos(reg.repos) });
  if (!project.repos.length && !charter && !fs.existsSync(path.join(dir, 'rules.md'))) throw new Error('agent-project の状態フォルダを選んでください');
  const items = [], excluded = [], seen = new Map();
  const add = (rel, group) => {
    const full = path.join(dir, rel);
    const info = fs.lstatSync(full);
    if (info.isSymbolicLink() || path.basename(rel).startsWith('.')) { excluded.push({ path: rel, reason: 'リンク・管理データ' }); return; }
    if (info.isDirectory()) {
      for (const entry of fs.readdirSync(full).sort()) add(path.posix.join(rel, entry), group);
      return;
    }
    if (!info.isFile() || !/\.(md|txt)$/i.test(rel) || info.size > 1024 * 1024) { excluded.push({ path: rel, reason: '文書以外・1 MB 超の資料' }); return; }
    const raw = readText(full);
    for (const entry of group === 'pending' ? pendingEntries(raw) : [{ text: raw, suffix: '' }]) {
      const source = rel + entry.suffix;
      const content = extract(entry.text, group, rel);
      if (!content || !content.replace(/^#+.*$/gm, '').trim()) { excluded.push({ path: source, reason: '再利用する本文なし' }); continue; }
      // コードの空白や見出しにも意味があるため、同じ本文の資料だけを一つにする。
      const fingerprint = content;
      const previous = seen.get(`${group}:${fingerprint}`);
      if (previous) { previous.sources.push(source); continue; }
      const item = {
        id: crypto.createHash('sha256').update(`${group}\0${source}\0${content}`).digest('hex').slice(0, 24),
        group, title: (/^#+\s+(.+)$/m.exec(content) || [])[1] || path.basename(rel),
        content, sources: [source], sourceBytes: info.size,
      };
      items.push(item); seen.set(`${group}:${fingerprint}`, item);
    }
  };
  for (const group of IMPORT_GROUPS) for (const rel of group.paths) {
    if (!fs.existsSync(path.join(dir, rel))) continue;
    if (rel === 'DELIVERY.md' && items.some(item => item.group === 'outcomes')) { excluded.push({ path: rel, reason: '完了記録と重複する索引' }); continue; }
    add(rel, group.id);
  }
  const roots = new Set(IMPORT_GROUPS.flatMap(group => group.paths));
  for (const entry of fs.readdirSync(dir)) if (!roots.has(entry)) excluded.push({ path: entry, reason: /^repos\./.test(entry) ? 'project.yaml に変換' : '実行ログ・制御情報などの対象外データ' });
  return { root: dir, source: reg.source, project, folder: projects.folderName(project.name), items,
    selected: bundle.recommended(items), excluded, repoPaths: hostYaml ? hostRepoPaths(hostYaml) : {} };
}

function apply(kb, planned, selected = planned.selected) {
  if (!Array.isArray(selected) || selected.some(id => !planned.items.some(item => item.id === id))) throw new Error('取り込み元が変更されています。もう一度選び直してください');
  const base = path.join(kb, projects.DIR, planned.folder);
  // 統合文書が別の資料と混ざらないよう、既存フォルダには書き込まない。
  if (fs.existsSync(base)) throw new Error(`同じ名前のフォルダが既にあります: ${projects.DIR}/${planned.folder}`);
  const result = bundle.build(planned.items, selected);
  const documents = result.documents;
  const links = documents.map(doc => `- [${doc.label}](${doc.file})`);
  const readme = `# ${planned.project.name}\n\nagent-project の資料を整理した参照用ナレッジです。未完了タスクは自動実行されません。\n\n${links.join('\n')}\n`;
  fs.mkdirSync(base, { recursive: true });
  const written = [];
  for (const doc of [...documents, { file: 'README.md', content: readme }]) {
    fs.writeFileSync(path.join(base, doc.file), doc.content, 'utf8');
    written.push(path.posix.join(projects.DIR, planned.folder, doc.file));
  }
  written.push(...projects.write(kb, planned.folder, planned.project));
  return { written: [...new Set(written)], skipped: [], ...result };
}

module.exports = { charterRepos, registry, toProjectRepos, hostRepoPaths, plan, apply, extract, pendingEntries };
