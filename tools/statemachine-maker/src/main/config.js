'use strict';

// 設定は userData の 1 ファイル。持つのは「最近開いたフォルダ」「スキルのフォルダ」「実行に使う
// エージェント」だけで、定義そのものは `.statemachine/` にしか置かない（このツールが無くても動く）。

const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  recentRoots: [],
  skillDir: '',
  agent: 'claude',          // run_machine.py の --agent（claude | copilot | kiro | anthropic）
  model: '',
};

function configPath(userData) {
  return path.join(userData, 'config.json');
}

function load(userData) {
  try {
    const parsed = JSON.parse(fs.readFileSync(configPath(userData), 'utf8'));
    return { ...DEFAULTS, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
  } catch {
    return { ...DEFAULTS };
  }
}

function save(userData, config) {
  const next = { ...DEFAULTS, ...(config || {}) };
  next.recentRoots = [...new Set((next.recentRoots || []).map((r) => String(r)).filter(Boolean))].slice(0, 10);
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(configPath(userData), `${JSON.stringify(next, null, 2)}\n`, 'utf8');
  return next;
}

function remember(userData, root) {
  const cfg = load(userData);
  cfg.recentRoots = [root, ...cfg.recentRoots.filter((r) => r !== root)];
  return save(userData, cfg);
}

module.exports = { DEFAULTS, load, save, remember };
