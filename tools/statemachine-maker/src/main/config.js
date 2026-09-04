'use strict';

// 設定は userData の 1 ファイル。持つのは「登録したフォルダ」「最後に選んだフォルダ」
// 「スキルのフォルダ」「動かすときに使う agent-tools の定義名」だけ。ステートマシンそのものは登録した
// フォルダの `.statemachine/` にしか置かない（このアプリが無くても動く）。

const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  roots: [],        // 登録したフォルダ。ここに無いフォルダは見に行かない
  lastRoot: '',     // 起動時に開くフォルダ
  skillDir: '',
  agent: 'aider',
  model: '',
};

const MAX_ROOTS = 30;

function configPath(userData) {
  return path.join(userData, 'config.json');
}

function normalize(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const next = { ...DEFAULTS, ...src };
  // 旧版は「最近開いたフォルダ」を持っていた。登録フォルダとして引き継ぐ。
  const legacy = Array.isArray(src.recentRoots) ? src.recentRoots : [];
  const roots = [...(Array.isArray(next.roots) ? next.roots : []), ...legacy];
  next.roots = [...new Set(roots.map((r) => String(r || '')).filter(Boolean))].slice(0, MAX_ROOTS);
  next.lastRoot = next.roots.includes(next.lastRoot) ? next.lastRoot : (next.roots[0] || '');
  delete next.recentRoots;
  return next;
}

function load(userData) {
  try {
    return normalize(JSON.parse(fs.readFileSync(configPath(userData), 'utf8')));
  } catch {
    return normalize(null);
  }
}

function save(userData, config) {
  const next = normalize({ ...load(userData), ...(config || {}) });
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(configPath(userData), `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  return next;
}

function addRoot(userData, root) {
  const cfg = load(userData);
  const dir = String(root || '');
  if (!dir) throw new Error('フォルダを選んでください');
  cfg.roots = [...cfg.roots.filter((r) => r !== dir), dir];
  cfg.lastRoot = dir;
  return save(userData, cfg);
}

function removeRoot(userData, root) {
  const cfg = load(userData);
  cfg.roots = cfg.roots.filter((r) => r !== String(root || ''));
  if (cfg.lastRoot === root) cfg.lastRoot = cfg.roots[0] || '';
  return save(userData, cfg);
}

// 登録したフォルダだけを見る。画面から届いたパスをそのまま信じない。
function isRegistered(userData, root) {
  return load(userData).roots.includes(String(root || ''));
}

module.exports = { DEFAULTS, load, save, addRoot, removeRoot, isRegistered, normalize };
