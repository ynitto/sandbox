'use strict';

// ノード予算（node-budget 契約）の読み書き。
// 正典: schemas/node-budget.schema.json。実体は $AGENT_BUDGET_DIR
// （既定 ~/.agent/budget/）の config.json + ledger/<YYYYMMDD>.jsonl（UTC・追記専用）。
// dashboard は「config を書き、台帳を読む」だけ — 記帳・抑制は各ツール
// （agent-amigos は実装済み。kiro-loop / agent-project / agent-flow は後続）が行う。
// 依頼側・請負側どちらのノードでも同じ契約で管理できる。

const fs = require('fs');
const os = require('os');
const path = require('path');

const KNOWN_WORKLOADS = ['routine', 'project', 'flow', 'amigos'];

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function resolveBudgetDir(cfg) {
  const c = (cfg && cfg.amigos) || {};
  return expandHome(
    c.budgetDir || process.env.AGENT_BUDGET_DIR || path.join(os.homedir(), '.agent', 'budget')
  );
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function loadBudgetConfig(dir) {
  const raw = readJson(path.join(dir, 'config.json')) || {};
  const workloads = {};
  for (const [k, v] of Object.entries(raw.workloads || {})) {
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) workloads[k] = n;
  }
  return {
    execution_minutes: Math.max(0, Number(raw.execution_minutes) || 0),
    period: ['day', 'month', 'total'].includes(raw.period) ? raw.period : 'day',
    workloads,
    exists: fs.existsSync(path.join(dir, 'config.json')),
  };
}

function utcStamp(kind) {
  const d = new Date();
  const y = String(d.getUTCFullYear());
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return kind === 'month' ? y + m : y + m + day;
}

function ledgerFiles(dir, period) {
  const ledger = path.join(dir, 'ledger');
  let names;
  try {
    names = fs.readdirSync(ledger).filter((n) => n.endsWith('.jsonl')).sort();
  } catch {
    return [];
  }
  if (period === 'day') names = names.filter((n) => n.slice(0, 8) === utcStamp('day'));
  else if (period === 'month') names = names.filter((n) => n.slice(0, 6) === utcStamp('month'));
  return names.map((n) => path.join(ledger, n));
}

function ledgerRecords(dir, period) {
  const out = [];
  for (const file of ledgerFiles(dir, period)) {
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    for (const line of text.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try {
        const rec = JSON.parse(s);
        if (rec && typeof rec === 'object') out.push(rec);
      } catch {
        /* 壊れた行は無視（追記専用台帳の書きかけ耐性） */
      }
    }
  }
  return out;
}

// 消費状況: ワークロード別の内訳・合計・超過判定（0 = 無制限）。
function usage(cfg) {
  const dir = resolveBudgetDir(cfg);
  const config = loadBudgetConfig(dir);
  const totals = {};
  let totalSeconds = 0;
  for (const rec of ledgerRecords(dir, config.period)) {
    const sec = Number(rec.seconds);
    if (!Number.isFinite(sec) || sec <= 0) continue;
    const wl = String(rec.workload || 'other');
    totals[wl] = (totals[wl] || 0) + sec;
    totalSeconds += sec;
  }
  const limitSeconds = config.execution_minutes * 60;
  const exceededWorkloads = [];
  for (const [wl, mins] of Object.entries(config.workloads)) {
    if (mins > 0 && (totals[wl] || 0) >= mins * 60) exceededWorkloads.push(wl);
  }
  const exceeded = (limitSeconds > 0 && totalSeconds >= limitSeconds) || exceededWorkloads.length > 0;
  const hasData = config.exists || totalSeconds > 0 || ledgerFiles(dir, 'total').length > 0;
  return {
    dir,
    config: {
      execution_minutes: config.execution_minutes,
      period: config.period,
      workloads: config.workloads,
    },
    knownWorkloads: KNOWN_WORKLOADS,
    totals,
    totalSeconds,
    limitSeconds,
    exceeded,
    exceededWorkloads,
    hasData,
  };
}

// 上限の保存（部分更新）。0 = 無制限。書くのは config.json だけ（台帳は各ツールの所有）。
function save(cfg, patch) {
  const dir = resolveBudgetDir(cfg);
  const cur = loadBudgetConfig(dir);
  const next = {
    version: 1,
    execution_minutes: cur.execution_minutes,
    period: cur.period,
    workloads: { ...cur.workloads },
  };
  if (patch && patch.executionMinutes !== undefined) {
    const n = Number(patch.executionMinutes);
    if (!Number.isFinite(n) || n < 0) throw new Error('合計上限（分）は 0 以上の数値で指定してください（0 = 無制限）');
    next.execution_minutes = n;
  }
  if (patch && patch.period !== undefined) {
    if (!['day', 'month', 'total'].includes(patch.period)) {
      throw new Error(`period が不正です: ${patch.period}（day / month / total）`);
    }
    next.period = patch.period;
  }
  if (patch && patch.workloads && typeof patch.workloads === 'object') {
    for (const [wl, v] of Object.entries(patch.workloads)) {
      const n = Number(v);
      if (!Number.isFinite(n) || n < 0) throw new Error(`内訳上限（${wl}）は 0 以上の数値で指定してください`);
      next.workloads[wl] = n;
    }
  }
  next.updated_at = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  next.updated_by = 'dashboard';
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, 'config.json');
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(next, null, 2)}\n`);
  fs.renameSync(tmp, target);
  return usage(cfg);
}

module.exports = { resolveBudgetDir, loadBudgetConfig, ledgerRecords, usage, save, KNOWN_WORKLOADS };
