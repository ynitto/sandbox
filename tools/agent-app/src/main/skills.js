'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

function entries(dir) {
  try { return fs.readdirSync(dir, { withFileTypes: true }); } catch { return []; }
}

function listFromRoots(roots) {
  const names = new Set();
  for (const root of Array.isArray(roots) ? roots : []) {
    const dir = String((root && root.path) || '');
    if (!dir) continue;
    for (const entry of entries(dir)) {
      if (root.kind === 'command-dir') {
        if (entry.isFile() && entry.name.endsWith('.md')) names.add(entry.name.slice(0, -3));
      } else if (entry.isDirectory() && fs.existsSync(path.join(dir, entry.name, 'SKILL.md'))) {
        names.add(entry.name);
      }
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

function list(repo = '') {
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
  return listFromRoots(roots);
}

module.exports = { list, listFromRoots };
