'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

function entries(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }); } catch { return []; }
}

function sourceRoots(repo = '') {
  const home = os.homedir();
  const roots = [
    { path: path.join(home, '.agents', 'skills'), kind: 'skill-dir' },
    { path: path.join(home, '.codex', 'skills'), kind: 'skill-dir' },
    { path: path.join(home, '.claude', 'commands'), kind: 'command-dir' },
    { path: path.join(home, '.kiro', 'commands'), kind: 'command-dir' },
  ];
  if (repo) roots.unshift(
    { path: path.join(repo, '.agents', 'skills'), kind: 'skill-dir' },
    { path: path.join(repo, '.codex', 'skills'), kind: 'skill-dir' },
  );
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
  return { name, description, tags, path: file, content };
}

function catalogFromRoots(roots) {
  const found = new Map();
  for (const root of Array.isArray(roots) ? roots : []) {
    const dir = String((root && root.path) || '');
    if (!dir) continue;
    for (const entry of entries(dir)) {
      let name = '';
      let file = '';
      if (root.kind === 'command-dir') {
        if (entry.isFile() && entry.name.endsWith('.md')) {
          name = entry.name.slice(0, -3);
          file = path.join(dir, entry.name);
        }
      } else if (entry.isDirectory() && fs.existsSync(path.join(dir, entry.name, 'SKILL.md'))) {
        name = entry.name;
        file = path.join(dir, entry.name, 'SKILL.md');
      }
      if (!name || found.has(name)) continue;
      try { found.set(name, metadata(name, file, fs.readFileSync(file, 'utf8'))); } catch { /* unreadable */ }
    }
  }
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function listFromRoots(roots) {
  return catalogFromRoots(roots).map((item) => item.name);
}

function list(repo = '') {
  return listFromRoots(sourceRoots(repo));
}

function catalog(repo = '') { return catalogFromRoots(sourceRoots(repo)); }

module.exports = { list, listFromRoots, catalog, catalogFromRoots };
