'use strict';

// セッション開始コマンド（agent-session-commands 契約）の読み書きと決定的な実行計画の組み立て。
// 正典: schemas/agent-session-commands.schema.json。実体は $AGENT_SESSION_DIR
// （既定 ~/.agents/session/）の session.json（管理面が原子書換）。
//
// dashboard は session.json に「セッション開始時に 1 回だけ走らせるコマンド列」を書き
// revision を単調増加させる。各エンジンはセッション開始点（常駐系は tmux ペイン生成、
// 単発系はワーカープロセス起動）でこれを読み、配列順に逐次実行する。
//
// instructions.js とほぼ同型だが、**委譲先ノードへ伝播しない**点だけが違う。任意コマンドの
// 到達範囲をこの端末に閉じ込めるため、agent-flow の meta.json にも GitBus にも載せない。
//
// ここでの plan()（プレースホルダ展開 + when 判定 + 有界化）は各エンジン（Python 側）と
// 同一結果になるよう決定的に保つ。UI のプレビューは「エンジンが実際に走らせるもの」である。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const DEFAULT_TIMEOUT = 60;
const DEFAULT_MAX_TOTAL_TIMEOUT = 120;
const HARD_MAX_TOTAL_TIMEOUT = 600;
const MODES = ['process', 'chat'];
const ON_ERRORS = ['warn', 'fail'];
const WHEN_KEYS = ['engines', 'workloads', 'agent_cli'];
// chat モードを送れるのは常駐系（セッションが長寿命なエンジン）だけ。
const CHAT_CAPABLE_ENGINES = ['kiro-loop', 'agent-loop', 'dashboard'];

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function resolveSessionDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  return expandHome(
    c.sessionDir || process.env.AGENT_SESSION_DIR || agentHomeSubdir('session')
  );
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function nowStamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function atomicWriteJson(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, target);
}

function clampMaxTotalTimeout(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MAX_TOTAL_TIMEOUT;
  return Math.min(Math.floor(n), HARD_MAX_TOTAL_TIMEOUT);
}

function clampTimeout(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_TIMEOUT;
  return Math.min(Math.floor(n), HARD_MAX_TOTAL_TIMEOUT);
}

// when を正規化。空配列・非配列は「その軸では絞らない」として捨てる。
function normalizeWhen(w) {
  if (!isPlainObject(w)) return null;
  const out = {};
  for (const key of WHEN_KEYS) {
    if (!Array.isArray(w[key])) continue;
    const values = w[key].map((v) => String(v || '').trim()).filter(Boolean);
    if (values.length) out[key] = values;
  }
  return Object.keys(out).length ? out : null;
}

// コマンド 1 件を正規化。id / run が無いものは捨てる（不完全な行を保存させない）。
function normalizeCommand(c) {
  if (!isPlainObject(c)) return null;
  const id = String(c.id || '').trim();
  const run = typeof c.run === 'string' ? c.run.trim() : '';
  if (!id || !run) return null;
  const mode = MODES.includes(c.mode) ? c.mode : 'process';
  const out = { id, mode, run };
  if (mode === 'process') {
    const cwd = String(c.cwd || '').trim();
    if (cwd) out.cwd = cwd;
    if (isPlainObject(c.env)) {
      const env = {};
      for (const [k, v] of Object.entries(c.env)) {
        const key = String(k || '').trim();
        if (key) env[key] = String(v == null ? '' : v);
      }
      if (Object.keys(env).length) out.env = env;
    }
    if (c.timeout !== undefined && c.timeout !== null && c.timeout !== '') {
      out.timeout = clampTimeout(c.timeout);
    }
  }
  out.on_error = ON_ERRORS.includes(c.on_error) ? c.on_error : 'warn';
  const when = normalizeWhen(c.when);
  if (when) out.when = when;
  return out;
}

// session.json を読む。無ければ既定（version:1, revision:0, enabled:true, コマンドなし）。
function loadSessionCommands(dir) {
  const raw = readJson(path.join(dir, 'session.json'));
  if (!isPlainObject(raw)) {
    return {
      version: 1,
      revision: 0,
      enabled: true,
      commands: [],
      max_total_timeout: DEFAULT_MAX_TOTAL_TIMEOUT,
    };
  }
  const commands = (Array.isArray(raw.commands) ? raw.commands : [])
    .map(normalizeCommand)
    .filter(Boolean);
  return {
    version: 1,
    revision: Number.isFinite(Number(raw.revision)) ? Number(raw.revision) : 0,
    enabled: raw.enabled !== false,
    commands,
    max_total_timeout: clampMaxTotalTimeout(raw.max_total_timeout),
    updated_at: raw.updated_at,
    updated_by: raw.updated_by,
    _raw: raw, // additive: 未知キーを保持し、書換時に土台とする
  };
}

// プレースホルダ展開。未定義は空文字へ落とす（エラーにしない）。**クォートは足さない** —
// 空白を含むパスの引用は利用者の責任（複合コマンドを書けるようにするための意図的な選択）。
function expandPlaceholders(text, ctx) {
  const c = isPlainObject(ctx) ? ctx : {};
  return String(text == null ? '' : text).replace(
    /\{(cwd|workspace|engine|workload|agent_cli|model|run_id|node_id)\}/g,
    (_, key) => String(c[key] == null ? '' : c[key])
  );
}

// when 判定。指定された軸をすべて満たすときだけ true（AND 結合）。
// 判定材料が ctx に無い軸は「絞れない」ので通す（フェイルセーフ側）。
function matchesWhen(when, ctx) {
  const w = normalizeWhen(when);
  if (!w) return true;
  const c = isPlainObject(ctx) ? ctx : {};
  const axes = [
    ['engines', c.engine],
    ['workloads', c.workload],
    ['agent_cli', c.agent_cli],
  ];
  for (const [key, value] of axes) {
    if (!w[key]) continue;
    const v = String(value == null ? '' : value).trim();
    if (!v) continue;
    if (!w[key].includes(v)) return false;
  }
  return true;
}

// 実行計画を組み立てる（決定的・副作用なし）。UI のプレビューとエンジンの実行が同じものを見る。
// 返す各要素は skip の理由を持つため、UI は除外された行もグレーで残せる。
function plan(data, ctx) {
  const c = isPlainObject(ctx) ? ctx : {};
  const engine = String(c.engine || '').trim();
  const out = [];
  if (!isPlainObject(data) || data.enabled === false) return out;
  const commands = Array.isArray(data.commands) ? data.commands : [];
  const budget = clampMaxTotalTimeout(data.max_total_timeout);
  let spent = 0;
  for (const item of commands) {
    const cmd = normalizeCommand(item);
    if (!cmd) continue;
    const entry = {
      id: cmd.id,
      mode: cmd.mode,
      run: expandPlaceholders(cmd.run, c),
      on_error: cmd.on_error,
      skip: null,
    };
    if (cmd.mode === 'process') {
      entry.cwd = cmd.cwd ? expandPlaceholders(cmd.cwd, c) : String(c.cwd || '');
      entry.timeout = cmd.timeout === undefined ? DEFAULT_TIMEOUT : cmd.timeout;
      if (cmd.env) {
        entry.env = {};
        for (const [k, v] of Object.entries(cmd.env)) entry.env[k] = expandPlaceholders(v, c);
      }
    }
    if (!matchesWhen(cmd.when, c)) {
      entry.skip = 'when';
    } else if (cmd.mode === 'chat' && engine && !CHAT_CAPABLE_ENGINES.includes(engine)) {
      // 単発系にはセッションが無い。黙って落とさず理由を残す（エンジン側もログに書く）。
      entry.skip = 'no-session';
    } else if (cmd.mode === 'process') {
      if (spent >= budget) {
        entry.skip = 'budget';
      } else {
        // 残り予算を超える timeout は残りへ切り詰める（合計の有界化）。
        entry.timeout = Math.min(entry.timeout, budget - spent);
        spent += entry.timeout;
      }
    }
    out.push(entry);
  }
  return out;
}

// patch をマージして session.json を書く。revision を +1 し updated_at/by を刻む。原子書換。
function saveSessionCommands(cfg, patch) {
  const dir = resolveSessionDir(cfg);
  const cur = loadSessionCommands(dir);
  const p = patch || {};
  const next = { ...(cur._raw || {}) }; // additive: 未知キーを保持
  next.version = 1;
  next.enabled = isPlainObject(cur._raw) ? cur.enabled : true;
  next.commands = Array.isArray(cur.commands) ? cur.commands.slice() : [];
  next.max_total_timeout = cur.max_total_timeout;

  if (p.enabled !== undefined) next.enabled = !!p.enabled;
  if (p.commands !== undefined) {
    if (!Array.isArray(p.commands)) throw new Error('commands は配列で指定してください');
    const out = [];
    const seen = new Set();
    for (const item of p.commands) {
      const n = normalizeCommand(item);
      if (!n) throw new Error('コマンドには ID と実行内容の両方が必要です');
      if (seen.has(n.id)) throw new Error(`コマンドの ID が重複しています: ${n.id}`);
      seen.add(n.id);
      out.push(n);
    }
    next.commands = out;
  }
  if (p.max_total_timeout !== undefined) {
    next.max_total_timeout = clampMaxTotalTimeout(p.max_total_timeout);
  }

  next.revision = cur.revision + 1;
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'session.json'), next);
  return loadSessionCommands(dir);
}

module.exports = {
  resolveSessionDir,
  loadSessionCommands,
  saveSessionCommands,
  normalizeCommand,
  expandPlaceholders,
  matchesWhen,
  plan,
  DEFAULT_TIMEOUT,
  DEFAULT_MAX_TOTAL_TIMEOUT,
  HARD_MAX_TOTAL_TIMEOUT,
  CHAT_CAPABLE_ENGINES,
  MODES,
  ON_ERRORS,
};
