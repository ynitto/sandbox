'use strict';

// エージェント制御（agent-control 契約）の読み書きと status/ 読取。
// 正典: schemas/agent-control.schema.json。実体は $AGENT_CONTROL_DIR
// （既定 ~/.agent/control/）の control.json（管理面が原子書換）と
// status/<tool>-<pid>.json（各エンジンがハートビート書換、管理面が読む）。
//
// dashboard は control.json に「望ましい状態」を書き revision を単調増加させる。
// 各エンジンは既存のチョークポイント / サイクル先頭で mtime を見て再読込・適用する（pull 型）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const LIFECYCLES = ['run', 'pause', 'stop'];
const DELEGATION_PREFER = ['local', 'remote'];

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function resolveControlDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  return expandHome(
    c.controlDir || process.env.AGENT_CONTROL_DIR || agentHomeSubdir('control')
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

// control.json を読む。無ければ既定（version:1, revision:0, defaults:{}, workloads:{}）。
function loadControl(dir) {
  const raw = readJson(path.join(dir, 'control.json'));
  if (!isPlainObject(raw)) {
    return { version: 1, revision: 0, defaults: {}, workloads: {} };
  }
  return {
    version: 1,
    revision: Number.isFinite(Number(raw.revision)) ? Number(raw.revision) : 0,
    defaults: isPlainObject(raw.defaults) ? raw.defaults : {},
    workloads: isPlainObject(raw.workloads) ? raw.workloads : {},
    updated_at: raw.updated_at,
    updated_by: raw.updated_by,
    // additive: 未知キーを保持し、書換時に土台とする
    _raw: raw,
  };
}

// agent_override（{agent_cli, model}）の正規化。null / 省略はそのまま（下位解決へ委ねる）。
function normalizeOverride(patch, base) {
  const out = isPlainObject(base) ? { ...base } : {};
  if (patch.agent_cli !== undefined) {
    out.agent_cli = patch.agent_cli === null || patch.agent_cli === '' ? null : String(patch.agent_cli);
  }
  if (patch.model !== undefined) {
    out.model = patch.model === null || patch.model === '' ? null : String(patch.model);
  }
  return out;
}

// 1 ワークロードへの指示を検証しつつ既存へ深くマージする。
function mergeWorkloadControl(base, patch) {
  const out = isPlainObject(base) ? { ...base } : {};
  if (patch.agent_cli !== undefined) {
    out.agent_cli = patch.agent_cli === null || patch.agent_cli === '' ? null : String(patch.agent_cli);
  }
  if (patch.model !== undefined) {
    out.model = patch.model === null || patch.model === '' ? null : String(patch.model);
  }
  if (patch.agents !== undefined) {
    if (!isPlainObject(patch.agents)) throw new Error('agents はオブジェクトで指定してください');
    const agents = isPlainObject(out.agents) ? { ...out.agents } : {};
    for (const [key, ov] of Object.entries(patch.agents)) {
      if (ov === null) {
        delete agents[key];
      } else {
        if (!isPlainObject(ov)) throw new Error(`agents.${key} はオブジェクトで指定してください`);
        agents[key] = normalizeOverride(ov, agents[key]);
      }
    }
    out.agents = agents;
  }
  if (patch.degraded !== undefined) {
    if (patch.degraded === null) delete out.degraded;
    else {
      if (!isPlainObject(patch.degraded)) throw new Error('degraded はオブジェクトで指定してください');
      out.degraded = normalizeOverride(patch.degraded, out.degraded);
    }
  }
  if (patch.lifecycle !== undefined) {
    if (!LIFECYCLES.includes(patch.lifecycle)) {
      throw new Error(`lifecycle が不正です: ${patch.lifecycle}（run / pause / stop）`);
    }
    out.lifecycle = patch.lifecycle;
  }
  if (patch.delegation !== undefined) {
    if (!isPlainObject(patch.delegation)) throw new Error('delegation はオブジェクトで指定してください');
    const delegation = isPlainObject(out.delegation) ? { ...out.delegation } : {};
    if (patch.delegation.prefer !== undefined) {
      if (!DELEGATION_PREFER.includes(patch.delegation.prefer)) {
        throw new Error(`delegation.prefer が不正です: ${patch.delegation.prefer}（local / remote）`);
      }
      delegation.prefer = patch.delegation.prefer;
    }
    if (patch.delegation.max_open_issues !== undefined) {
      const n = Number(patch.delegation.max_open_issues);
      if (!Number.isFinite(n) || n < 0) throw new Error('delegation.max_open_issues は 0 以上で指定してください');
      delegation.max_open_issues = n;
    }
    out.delegation = delegation;
  }
  return out;
}

// control.json へ patch をマージして書く。revision を +1 し updated_at/by を刻む。原子書換。
function saveControl(cfg, patch) {
  const dir = resolveControlDir(cfg);
  const cur = loadControl(dir);
  const p = patch || {};
  const next = { ...(cur._raw || {}) }; // additive: 未知キーを保持
  next.version = 1;
  next.defaults = isPlainObject(cur.defaults) ? { ...cur.defaults } : {};
  next.workloads = isPlainObject(cur.workloads) ? { ...cur.workloads } : {};

  if (p.defaults !== undefined) {
    if (!isPlainObject(p.defaults)) throw new Error('defaults はオブジェクトで指定してください');
    next.defaults = normalizeOverride(p.defaults, next.defaults);
  }
  if (p.workloads !== undefined) {
    if (!isPlainObject(p.workloads)) throw new Error('workloads はオブジェクトで指定してください');
    for (const [w, wc] of Object.entries(p.workloads)) {
      if (!isPlainObject(wc)) throw new Error(`workloads.${w} はオブジェクトで指定してください`);
      next.workloads[w] = mergeWorkloadControl(next.workloads[w], wc);
    }
  }
  next.revision = cur.revision + 1;
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'control.json'), next);
  return loadControl(dir);
}

// lifecycle の近道: workloads[workload].lifecycle=action にして revision を +1。
function setLifecycle(cfg, payload) {
  const p = payload || {};
  const workload = String(p.workload || '').trim();
  if (!workload) throw new Error('workload が必要です');
  if (!LIFECYCLES.includes(p.action)) {
    throw new Error(`action が不正です: ${p.action}（run / pause / stop）`);
  }
  return saveControl(cfg, { workloads: { [workload]: { lifecycle: p.action } } });
}

// status/*.json を読み、各記録に fresh 判定を付けて返す。欠損・破損は寛容に無視。
function readStatus(dir) {
  const statusDir = path.join(dir, 'status');
  let names;
  try {
    names = fs.readdirSync(statusDir).filter((n) => n.endsWith('.json'));
  } catch {
    return [];
  }
  const now = Date.now();
  const out = [];
  for (const name of names) {
    const rec = readJson(path.join(statusDir, name));
    if (!isPlainObject(rec)) continue;
    const freshAfter = Number(rec.fresh_after_sec) > 0 ? Number(rec.fresh_after_sec) : 120;
    const ts = Date.parse(rec.ts);
    // 反映遅延・時計ずれを考慮して 3 倍まで許容（generous）。
    const fresh = Number.isFinite(ts) ? now - ts <= freshAfter * 1000 * 3 : false;
    out.push({ ...rec, file: name, fresh });
  }
  return out;
}

module.exports = { resolveControlDir, loadControl, saveControl, setLifecycle, readStatus };
