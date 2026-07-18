'use strict';

// amigos ホーム（常駐デーモンの稼働ディレクトリ）の発見と、指示の投函。
//
// - ホーム = `agent-amigos.{yaml,yml,json}` または `.agent/agent-amigos.*` を持つ
//   ディレクトリ（設定ファイルが dashboard の自動発見マーカーを兼ねる —
//   agent-project と同じ流儀）。`amigos.homeDirs` の明示指定 + 全体設定
//   `projects.roots` 配下の走査で見つける。
// - タスク依頼（post）・手動引き受け（claim）は、ホームの
//   `.agent/agent-amigos/commands/*.json` へ JSON を 1 ファイル置くだけ（agent-project の
//   commands/ と同じ結合方式）。常駐デーモンが次のサイクルで取り込む。
//   dashboard はバスへ直接書かない — 書くのは常にホームの commands ドロップだけ。

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');

const CONFIG_NAMES = ['agent-amigos.yaml', 'agent-amigos.yml', 'agent-amigos.json'];

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

function isTruthy(v) {
  if (v === true || v === 1) return true;
  const s = String(v == null ? '' : v).trim().toLowerCase();
  return s === 'true' || s === 'yes' || s === 'on' || s === '1';
}

function readConfigFile(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
  if (file.endsWith('.json')) {
    try {
      const obj = JSON.parse(text);
      if (obj && typeof obj === 'object') return { file, values: obj };
    } catch {
      return null;
    }
    return null;
  }
  // YAML はトップレベルのスカラだけ読む（bus / bus_workdir / node_id / manual_claim で足りる。
  // hub: 等のネストは daemon 側の関心で dashboard は読まない）
  return { file, values: parseFlatYaml(text) };
}

function readConfig(dir) {
  // ルート直下を .agent/ より先に見る（agent-project / agent-amigos の探索順に合わせる）
  for (const name of CONFIG_NAMES) {
    const found = readConfigFile(path.join(dir, name));
    if (found) return found;
  }
  for (const name of CONFIG_NAMES) {
    const found = readConfigFile(path.join(dir, '.agent', name));
    if (found) return found;
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
    if (busSpec.startsWith('git+') || busSpec.startsWith('hub+')) {
      // GitBus / HubBus はローカルミラー（workdir）がバスの実体。agent-amigos と同じ導出:
      // 設定 bus_workdir、無ければ ~/.agent/amigos/{bus|hub}/<sha1(url)[:8]>（gitbus.py / hubbus.py）。
      // これが無いとミッション → ホームの対応（引き受け・依頼の投函先解決）が git/hub バスで切れる。
      if (values.bus_workdir) {
        const p = expandHome(String(values.bus_workdir));
        busDir = path.isAbsolute(p) ? p : path.resolve(dir, p);
      } else {
        const url = busSpec.slice(4);
        const digest = crypto.createHash('sha1').update(url, 'utf8').digest('hex').slice(0, 8);
        const kind = busSpec.startsWith('git+') ? 'bus' : 'hub';
        busDir = path.join(os.homedir(), '.agent', 'amigos', kind, digest);
      }
    } else {
      const p = expandHome(busSpec);
      busDir = path.isAbsolute(p) ? p : path.resolve(dir, p);
    }
    homes.push({
      dir,
      configFile: conf ? conf.file : null,
      busSpec,
      busDir,
      nodeId: values.node_id ? String(values.node_id) : null,
      manualClaim: isTruthy(values.manual_claim),
      commandsDir: path.join(dir, '.agent', 'agent-amigos', 'commands'),
    });
  }
  return homes;
}

// 投函できるコマンド。契約の正典は schemas/amigos-command.schema.json（取り込み側は
// agent_amigos/commands.py の _dispatch）。両者の一致はテストで担保する。
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

module.exports = { discoverHomes, writeCommand, readConfig, ALLOWED_COMMANDS };
