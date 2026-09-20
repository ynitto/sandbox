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

function metadata(name, file, content) {
  const front = String(content || '').match(/^---\s*\n([\s\S]*?)\n---(?:\s*\n|$)/);
  const header = front ? front[1] : '';
  const descriptionLine = header.match(/^description:\s*([^\n]*)/m);
  const rawDescription = ((descriptionLine || [])[1] || '').trim();
  let description = rawDescription.replace(/^['"]|['"]$/g, '');
  if (rawDescription === '|' || rawDescription === '>') {
    const after = header.slice((descriptionLine.index || 0) + descriptionLine[0].length).replace(/^\n/, '');
    description = [];
    for (const line of after.split('\n')) {
      if (!/^\s+/.test(line)) break;
      description.push(line.trim());
    }
    description = description.filter(Boolean).join(' ');
  }
  const tagsBlock = (header.match(/^tags:\s*\n((?:\s+-[^\n]*\n?)*)/m) || [])[1] || '';
  const tags = [...tagsBlock.matchAll(/^\s+-\s*(.+)$/gm)].map((match) => match[1].trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
  return { name, description, tags, version: readVersion(content), path: file, content };
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
