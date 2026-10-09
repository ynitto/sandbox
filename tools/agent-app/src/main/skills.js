'use strict';

// スキルの置き場を歩いて、名前・説明・版・場所を拾う。
//
// 置き場は 2 種類ある。**共通**（どの AI も読む）と、**その AI だけ**が読むもの。
// 画面で AI を選ぶと「その AI の置き場 + 共通の置き場」を出す（設定 > スキル）。
// 依頼に添えるスキルの自動選択（skillSelection.js）は AI を絞らないので、
// agent を渡さなければ今までどおり全部の置き場を見る。

const fs = require('fs');
const os = require('os');
const path = require('path');
const YAML = require('yaml');
const { readVersion } = require('./skillVersion');

// 走査の上限。置き場に何万も入っている PC で画面を止めない。
const MAX_ENTRIES = 400;

function entries(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }); } catch { return []; }
}

// その AI だけが読む置き場（ホーム / リポジトリの順に足す）。
const AGENT_DIRS = {
  claude: [['.claude', 'skills', 'skill-dir'], ['.claude', 'commands', 'command-dir']],
  codex: [['.codex', 'skills', 'skill-dir']],
  kiro: [['.kiro', 'commands', 'command-dir']],
  copilot: [['.github', 'skills', 'skill-dir']],
};

// どの AI も読む置き場。
const COMMON_DIRS = [['.agents', 'skills', 'skill-dir']];

function agentKey(agent) {
  const name = String(agent || '').toLowerCase();
  return Object.keys(AGENT_DIRS).find((key) => name === key || name.startsWith(`${key}-`) || name.includes(key)) || '';
}

// repo … 選択中のリポジトリ（無ければ ''）。agent … '' なら全部の置き場。
function sourceRoots(repo = '', agent = '') {
  const home = os.homedir();
  const key = agentKey(agent);
  // 未対応の AI を選んだときも、他の AI の置き場へ範囲を広げない。
  const specific = String(agent || '').trim() ? (AGENT_DIRS[key] || []) : Object.values(AGENT_DIRS).flat();
  const shapes = [...specific, ...COMMON_DIRS];
  const roots = [];
  // リポジトリの中を先に置く（同じ名前なら、その仕事の分を優先する）。
  if (repo) for (const [a, b, kind] of shapes) roots.push({ path: path.join(repo, a, b), kind, place: 'repo', repo });
  for (const [a, b, kind] of shapes) roots.push({ path: path.join(home, a, b), kind, place: 'home', repo: '' });
  return roots;
}

function unquote(value) {
  return String(value || '').trim().replace(/^['"]|['"]$/g, '');
}

// frontmatter 全体から「トップレベル tags」または「metadata の直下の tags」だけを読む。
// description: | の本文などにたまたま "tags:" が書かれていても、メタデータとして拾わない。
function tagsFromHeader(header) {
  const lines = String(header || '').split('\n');
  const readList = (index, indent) => {
    const values = [];
    for (let i = index + 1; i < lines.length; i += 1) {
      const line = lines[i];
      if (!line.trim()) continue;
      const spaces = (line.match(/^\s*/) || [''])[0].length;
      if (spaces <= indent) break;
      const item = line.match(/^\s*-\s*(.+)$/);
      if (!item) break;
      const value = unquote(item[1]);
      if (value) values.push(value);
    }
    return values;
  };

  for (let i = 0; i < lines.length; i += 1) {
    if (/^tags:\s*$/.test(lines[i])) return readList(i, 0);
  }

  const metadata = lines.findIndex((line) => /^metadata:\s*$/.test(line));
  if (metadata < 0) return [];
  let childIndent = null;
  for (let i = metadata + 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) continue;
    const spaces = (line.match(/^\s*/) || [''])[0].length;
    if (spaces === 0) break;
    if (childIndent == null) childIndent = spaces;
    if (spaces === childIndent && /^\s+tags:\s*$/.test(line)) return readList(i, spaces);
  }
  return [];
}

// YAML として読める frontmatter は YAML に任せる。`description: 使い方は "x" と "y"` の
// 末尾の引用符を落とさず、`tags: [a, b]` の一行形式や折り返した説明も読める。
// 読めない（壊れた）frontmatter は null を返し、呼び出し側が行ごとの読みに戻る。
function fieldsFromYaml(header) {
  try {
    const doc = YAML.parseDocument(header);
    if (doc.errors.length) return null;
    const data = doc.toJS();
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const text = (value) => (typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '');
    // `tags: ui, ux` のようにカンマ区切りの 1 行で書かれていても読む。
    const list = (value) => (Array.isArray(value) ? value : typeof value === 'string' ? value.split(/[,、]/) : [])
      .filter((item) => typeof item === 'string' || typeof item === 'number')
      .map((item) => String(item).trim()).filter(Boolean);
    const meta = data.metadata && typeof data.metadata === 'object' ? data.metadata : {};
    const tags = data.tags != null ? list(data.tags) : list(meta.tags);
    return { description: text(data.description), tags };
  } catch { return null; }
}

function metadata(name, file, content) {
  // Windows で書かれた SKILL.md（BOM・CRLF）でも、説明とタグを空にしない。
  const text = String(content || '').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  const front = text.match(/^---[^\S\n]*\n([\s\S]*?)\n---(?:\s*\n|$)/);
  const header = front ? front[1] : '';
  const descriptionLine = header.match(/^description:\s*([^\n]*)/m);
  const rawDescription = ((descriptionLine || [])[1] || '').trim();
  let description = unquote(rawDescription);
  if (/^[|>][+-]?$/.test(rawDescription)) {
    const after = header.slice((descriptionLine.index || 0) + descriptionLine[0].length).replace(/^\n/, '');
    description = [];
    for (const line of after.split('\n')) {
      // ブロックの途中の空行（段落の区切り）で説明を打ち切らない。
      if (line.trim() && !/^\s+/.test(line)) break;
      description.push(line.trim());
    }
    description = description.filter(Boolean).join(' ');
  }
  let tags = tagsFromHeader(header);
  const parsed = fieldsFromYaml(header);
  if (parsed) {
    if (parsed.description || !description) description = parsed.description;
    tags = parsed.tags;
  }
  return { name, description, tags, frontmatter: header, version: readVersion(content), path: file, content };
}

function catalogFromRoots(roots) {
  const found = new Map();
  for (const root of Array.isArray(roots) ? roots : []) {
    const dir = String((root && root.path) || '');
    if (!dir) continue;
    for (const entry of entries(dir)) {
      if (found.size >= MAX_ENTRIES) break;
      let name = '';
      let file = '';
      let home = '';
      if (root.kind === 'command-dir') {
        if (entry.isFile() && entry.name.endsWith('.md')) {
          name = entry.name.slice(0, -3);
          file = path.join(dir, entry.name);
          home = file;
        }
      } else if (entry.isDirectory() && fs.existsSync(path.join(dir, entry.name, 'SKILL.md'))) {
        name = entry.name;
        file = path.join(dir, entry.name, 'SKILL.md');
        home = path.join(dir, entry.name);
      }
      if (!name || found.has(name)) continue;
      try {
        found.set(name, {
          ...metadata(name, file, fs.readFileSync(file, 'utf8')),
          place: root.place || '', repo: root.repo || '', dir: home,
        });
      } catch { /* unreadable */ }
    }
  }
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function listFromRoots(roots) {
  return catalogFromRoots(roots).map((item) => item.name);
}

function list(repo = '', agent = '') {
  return listFromRoots(sourceRoots(repo, agent));
}

function catalog(repo = '', agent = '') { return catalogFromRoots(sourceRoots(repo, agent)); }

module.exports = { list, listFromRoots, catalog, catalogFromRoots, sourceRoots, AGENT_DIRS, COMMON_DIRS };
