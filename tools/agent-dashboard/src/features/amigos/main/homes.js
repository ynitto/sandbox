'use strict';

// amigos ホーム（常駐デーモンの稼働ディレクトリ）の発見と、指示の投函。
//
// - ホーム = `.kiro/kiro-amigos.{yaml,yml,json}` を持つディレクトリ（設定ファイルが
//   dashboard の自動発見マーカーを兼ねる — kiro-loop の `.kiro/kiro-loop.*` と同じ流儀）。
//   `amigos.homeDirs` の明示指定 + 全体設定 `projects.roots` 配下の走査で見つける。
// - タスク依頼（post）・手動引き受け（claim）は、ホームの
//   `.kiro/kiro-amigos/commands/*.json` へ JSON を 1 ファイル置くだけ（agent-project の
//   commands/ と同じ結合方式）。常駐デーモンが次のサイクルで取り込む。
//   dashboard はバスへ直接書かない — 書くのは常にホームの commands ドロップだけ。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');

const CONFIG_NAMES = ['kiro-amigos.yaml', 'kiro-amigos.yml', 'kiro-amigos.json'];

// 走査を軽く保つためのスキップ（cowork の discover と同じ発想）
const SCAN_SKIP = new Set([
  'node_modules', 'dist', 'release', 'build', 'out', 'coverage', 'bus', 'work',
  'archive', 'backlog', 'needs', 'decisions', 'commands', 'inbox', 'runs',
  'missions', 'artifacts', 'deliverable', 'vendor', 'target',
]);

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function readConfig(dir) {
  for (const name of CONFIG_NAMES) {
    const file = path.join(dir, '.kiro', name);
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    if (name.endsWith('.json')) {
      try {
        const obj = JSON.parse(text);
        if (obj && typeof obj === 'object') return { file, values: obj };
      } catch {
        continue;
      }
    }
    // YAML はトップレベルのスカラだけ読む（bus / node_id / manual_claim で足りる。
    // hub: 等のネストは daemon 側の関心で dashboard は読まない）
    return { file, values: parseFlatYaml(text) };
  }
  return null;
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function scanRoots(roots, depth) {
  const found = [];
  const walk = (dir, remain) => {
    if (readConfig(dir)) {
      found.push(dir);
      return; // ホームの下にホームは探さない
    }
    if (remain <= 0) return;
    let names;
    try {
      names = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of names) {
      if (!e.isDirectory() || e.name.startsWith('.') || SCAN_SKIP.has(e.name)) continue;
      walk(path.join(dir, e.name), remain - 1);
    }
  };
  for (const r of roots) {
    const root = path.resolve(expandHome(String(r || '')));
    if (isDir(root)) walk(root, depth);
  }
  return found;
}

// ホーム一覧: [{dir, configFile, busSpec, busDir|null, nodeId, manualClaim, commandsDir}]
function discoverHomes(cfg) {
  const a = (cfg && cfg.amigos) || {};
  const explicit = (Array.isArray(a.homeDirs) ? a.homeDirs : [])
    .map((d) => path.resolve(expandHome(String(d || ''))))
    .filter(Boolean);
  const roots = (cfg && cfg.projects && cfg.projects.roots) || [];
  const depth = Math.max(1, Number((cfg && cfg.projects && cfg.projects.scanDepth) || 2));
  const dirs = [...new Set([...explicit, ...scanRoots(roots, depth)])];
  const homes = [];
  for (const dir of dirs) {
    const conf = readConfig(dir);
    if (!conf && !explicit.includes(dir)) continue;
    const values = (conf && conf.values) || {};
    const busSpec = String(values.bus || '.');
    let busDir = null;
    if (!busSpec.startsWith('git+') && !busSpec.startsWith('hub+')) {
      const p = expandHome(busSpec);
      busDir = path.isAbsolute(p) ? p : path.resolve(dir, p);
    }
    homes.push({
      dir,
      configFile: conf ? conf.file : null,
      busSpec,
      busDir,
      nodeId: values.node_id ? String(values.node_id) : null,
      manualClaim: String(values.manual_claim) === 'true',
      commandsDir: path.join(dir, '.kiro', 'kiro-amigos', 'commands'),
    });
  }
  return homes;
}

const ALLOWED_COMMANDS = new Set(['post', 'claim', 'assign', 'accept', 'reject', 'cancel', 'say']);

// 指示の投函: ホーム検証（発見済みのホームのみ）→ commands/ へアトミックに 1 ファイル書く。
function writeCommand(cfg, homeDir, rec) {
  const home = discoverHomes(cfg).find(
    (h) => path.resolve(h.dir) === path.resolve(String(homeDir || ''))
  );
  if (!home) throw new Error(`amigos ホームではありません: ${homeDir}`);
  if (!rec || !ALLOWED_COMMANDS.has(rec.command)) {
    throw new Error(`不正なコマンドです: ${rec && rec.command}`);
  }
  fs.mkdirSync(home.commandsDir, { recursive: true });
  const name = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`;
  const target = path.join(home.commandsDir, name);
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(rec, null, 2)}\n`);
  fs.renameSync(tmp, target);
  return { home: home.dir, file: target };
}

module.exports = { discoverHomes, writeCommand, readConfig };
