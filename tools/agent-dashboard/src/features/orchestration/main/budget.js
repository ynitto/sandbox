'use strict';

// ノード予算（node-budget v2 契約）の読み書き・集計・配分計算・レート較正。
// 正典: schemas/node-budget.schema.json。実体は $AGENT_BUDGET_DIR
// （既定 ~/.agents/budget/）の config.json + ledger/<YYYYMMDD>.jsonl（UTC・追記専用）。
//
// v1（amigos/main/budget.js）を additive に拡張したもの。一次単位を実行時間（分）から
// トークンへ広げ、配分（allocation/computed）の知能を管理面（dashboard）に置く。
// dashboard は「config を書き、台帳を読む」だけ — 記帳・抑制は各エンジンが行う。
//
// 消費集計（読み出し側の共通規則）:
//   tokens(row) = tokens_in + tokens_out                （実測がある行）
//               | seconds × rate(agent_cli, model)      （無い行。読み出し時に推定）
// 台帳には事実（実測）だけを書き、推定は集計側で行う（レート表の改善が過去にも一貫して効く）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const KNOWN_WORKLOADS = ['routine', 'project', 'flow', 'amigos', 'audit', 'dashboard'];
const ON_EXHAUSTED = ['pause', 'stop', 'degrade'];
const ALLOC_MODES = ['static', 'auto'];

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function resolveBudgetDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  return expandHome(
    c.budgetDir || process.env.AGENT_BUDGET_DIR || agentHomeSubdir('budget')
  );
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

// 部分更新を config.json（契約形式）へ書く共通の原子書換（tmp → rename）。
function atomicWriteJson(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, target);
}

function nowStamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function loadBudgetConfig(dir) {
  const raw = readJson(path.join(dir, 'config.json')) || {};
  const workloads = {};
  for (const [k, v] of Object.entries(raw.workloads || {})) {
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) workloads[k] = n;
  }
  return {
    version: raw.version === 2 ? 2 : 1,
    execution_minutes: Math.max(0, Number(raw.execution_minutes) || 0),
    period: ['day', 'month', 'total'].includes(raw.period) ? raw.period : 'day',
    workloads,
    tokens: Math.max(0, Number(raw.tokens) || 0),
    allocation: isPlainObject(raw.allocation) ? raw.allocation : {},
    computed: isPlainObject(raw.computed) ? raw.computed : {},
    rates: isPlainObject(raw.rates) ? raw.rates : {},
    exists: fs.existsSync(path.join(dir, 'config.json')),
    // additive evolution: 未知キーを落とさないよう原本を保持し、書換時にマージ土台にする。
    raw,
  };
}

// レート解決（tokens/秒）。per_cli["cli:model"] → per_cli["cli"] → default_tokens_per_second → 0。
function rate(cfg, cli, model) {
  const rates = (cfg && cfg.rates) || {};
  const perCli = isPlainObject(rates.per_cli) ? rates.per_cli : {};
  const pick = (key) => {
    if (Object.prototype.hasOwnProperty.call(perCli, key)) {
      const n = Number(perCli[key]);
      if (Number.isFinite(n) && n >= 0) return n;
    }
    return null;
  };
  const c = cli ? String(cli) : '';
  const m = model !== undefined && model !== null && String(model) !== '' ? String(model) : '';
  if (c && m) {
    const v = pick(`${c}:${m}`);
    if (v !== null) return v;
  }
  if (c) {
    const v = pick(c);
    if (v !== null) return v;
  }
  const def = Number(rates.default_tokens_per_second);
  return Number.isFinite(def) && def >= 0 ? def : 0;
}

// 実測トークンが入っているか（1 つでもあれば実測行として扱う）。
function isMeasured(rec) {
  return (
    (rec.tokens_in !== undefined && rec.tokens_in !== null) ||
    (rec.tokens_out !== undefined && rec.tokens_out !== null)
  );
}

// 1 行のトークン消費。実測があればその合算、無ければ seconds × rate の推定。
function rowTokens(rec, cfg) {
  if (isMeasured(rec)) {
    return Math.max(0, Number(rec.tokens_in) || 0) + Math.max(0, Number(rec.tokens_out) || 0);
  }
  const sec = Number(rec.seconds);
  const s = Number.isFinite(sec) && sec > 0 ? sec : 0;
  return s * rate(cfg, rec.agent_cli, rec.model);
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

// ワークロード w の実効トークン上限。computed が優先、無ければ allocation.max_tokens、無ければ 0（無制限）。
function effectiveCap(config, wl) {
  const comp = (isPlainObject(config.computed.workloads) ? config.computed.workloads : {})[wl] || {};
  const c = Number(comp.tokens);
  if (Number.isFinite(c) && c > 0) return c;
  const alloc = (isPlainObject(config.allocation.workloads) ? config.allocation.workloads : {})[wl] || {};
  const m = Number(alloc.max_tokens);
  if (Number.isFinite(m) && m > 0) return m;
  return 0;
}

function softRatioOf(config) {
  const n = Number((config.allocation || {}).soft_ratio);
  return Number.isFinite(n) && n > 0 && n <= 1 ? n : 0.9;
}

// reset_at を書けなかった rate_limit の既定 TTL（秒）。CLI がメッセージに復帰時刻を載せない
// ことは普通にあるので、**復帰時刻不明を「永久に使えない」にしない**ための上限。
const QUOTA_RATE_LIMIT_TTL_SEC = 3600;

// 台帳の quota 観測行（quota_kind を持つ行）から、CLI ごとの「いま枠に当たっているか」を組む。
//
// **状態ファイルを別に持たない。** 観測はエンジンが台帳へ追記し、判定は読み手がこの関数で
// 毎回組み直す。書き手を増やさず（C7）、古い観測の掃除も要らない——台帳の読み出しは period
// 窓なので、`exhausted`（次の期間まで戻らない枠）はちょうど期間の切り替わりで消える。
function quotaAgentsFrom(records) {
  const out = {};
  for (const rec of records) {
    const kind = String(rec.quota_kind || '');
    if (kind !== 'exhausted' && kind !== 'rate_limit') continue;   // 他の観測種別（昇格など）は無関係
    const cli = String(rec.agent_cli || '');
    if (!cli) continue;
    const observedMs = Date.parse(rec.ts || '');
    const prev = out[cli];
    // 同じ CLI の観測が複数あれば最後の 1 件が現在の状態（枯渇 → 復帰 → 再枯渇 を順に反映）。
    if (prev && Number.isFinite(prev._observedMs)
        && Number.isFinite(observedMs) && observedMs < prev._observedMs) continue;
    out[cli] = {
      quota_kind: kind,
      observed_at: String(rec.ts || ''),
      _observedMs: observedMs,
      ...(rec.reset_at ? { reset_at: String(rec.reset_at) } : {}),
    };
  }
  for (const entry of Object.values(out)) {
    // 復帰時刻が読めない rate_limit は観測時刻 + 既定 TTL で失効させる。
    if (entry.quota_kind === 'rate_limit' && !Number.isFinite(Date.parse(entry.reset_at || ''))) {
      entry.reset_at = Number.isFinite(entry._observedMs)
        ? new Date(entry._observedMs + QUOTA_RATE_LIMIT_TTL_SEC * 1000).toISOString()
        : new Date(0).toISOString();  // 観測時刻も読めない = 判断材料が無いので塞がない
    }
    delete entry._observedMs;
  }
  return out;
}

// 消費状況: ワークロード別の秒・トークン（実測/推定の内訳）・実効上限・超過/縮退判定（0 = 無制限）。
function usage(cfg) {
  const dir = resolveBudgetDir(cfg);
  const config = loadBudgetConfig(dir);
  const seconds = {};
  const recordCounts = {};
  const unestimatedRecords = {};
  const agentTotals = {};
  const measuredTokens = {};
  const estimatedTokens = {};
  let totalSeconds = 0;
  let totalRecords = 0;
  let totalUnestimatedRecords = 0;
  let totalMeasured = 0;
  let totalEstimated = 0;
  const records = ledgerRecords(dir, config.period);
  const quotaAgents = quotaAgentsFrom(records);
  for (const rec of records) {
    // 観測行（quota・モデル昇格など）は消費ではない。秒・トークンだけでなく**実行回数にも
    // 数えない**——コストを測るために書いた行が、コストの平均を動かしてしまう。
    if (rec.event || rec.quota_kind) continue;
    const wl = String(rec.workload || 'other');
    const agent = String(rec.agent_cli || 'unknown');
    const agentTotal = agentTotals[agent] || (agentTotals[agent] = {
      seconds: 0, recordCount: 0, unestimatedRecords: 0,
      measuredTokens: 0, estimatedTokens: 0,
    });
    recordCounts[wl] = (recordCounts[wl] || 0) + 1;
    agentTotal.recordCount += 1;
    totalRecords += 1;
    const sec = Number(rec.seconds);
    if (Number.isFinite(sec) && sec > 0) {
      seconds[wl] = (seconds[wl] || 0) + sec;
      agentTotal.seconds += sec;
      totalSeconds += sec;
    }
    const tok = rowTokens(rec, config);
    if (!(tok > 0)) {
      if (!isMeasured(rec) && Number.isFinite(sec) && sec > 0) {
        unestimatedRecords[wl] = (unestimatedRecords[wl] || 0) + 1;
        agentTotal.unestimatedRecords += 1;
        totalUnestimatedRecords += 1;
      }
      continue;
    }
    if (isMeasured(rec)) {
      measuredTokens[wl] = (measuredTokens[wl] || 0) + tok;
      agentTotal.measuredTokens += tok;
      totalMeasured += tok;
    } else {
      estimatedTokens[wl] = (estimatedTokens[wl] || 0) + tok;
      agentTotal.estimatedTokens += tok;
      totalEstimated += tok;
    }
  }

  const softRatio = softRatioOf(config);
  const allWl = new Set([
    ...KNOWN_WORKLOADS,
    ...Object.keys(recordCounts),
    ...Object.keys(seconds),
    ...Object.keys(measuredTokens),
    ...Object.keys(estimatedTokens),
    ...Object.keys(config.workloads),
    ...Object.keys(isPlainObject(config.allocation.workloads) ? config.allocation.workloads : {}),
    ...Object.keys(isPlainObject(config.computed.workloads) ? config.computed.workloads : {}),
  ]);

  const workloads = {};
  const exceededWorkloads = []; // v1 互換: 時間（分）内訳上限の超過
  const tokenExceededWorkloads = [];
  const softWorkloads = [];
  for (const wl of allWl) {
    const secs = seconds[wl] || 0;
    const mt = measuredTokens[wl] || 0;
    const et = estimatedTokens[wl] || 0;
    const tt = mt + et;
    const cap = effectiveCap(config, wl);
    const timeLimitMin = Number(config.workloads[wl] || 0);
    const timeExceeded = timeLimitMin > 0 && secs >= timeLimitMin * 60;
    const tokenExceeded = cap > 0 && tt >= cap;
    const soft = cap > 0 && tt >= softRatio * cap;
    if (timeExceeded) exceededWorkloads.push(wl);
    if (tokenExceeded) tokenExceededWorkloads.push(wl);
    if (soft) softWorkloads.push(wl);
    workloads[wl] = {
      seconds: secs,
      recordCount: recordCounts[wl] || 0,
      unestimatedRecords: unestimatedRecords[wl] || 0,
      measuredTokens: mt,
      estimatedTokens: et,
      totalTokens: tt,
      timeLimitSeconds: timeLimitMin * 60,
      tokenCap: cap,
      timeExceeded,
      tokenExceeded,
      exceeded: timeExceeded || tokenExceeded,
      soft,
    };
  }

  const limitSeconds = config.execution_minutes * 60;
  const tokenLimit = config.tokens;
  const totalTokens = totalMeasured + totalEstimated;
  const timeExceededTotal = limitSeconds > 0 && totalSeconds >= limitSeconds;
  const tokenExceededTotal = tokenLimit > 0 && totalTokens >= tokenLimit;
  const exceeded =
    timeExceededTotal ||
    tokenExceededTotal ||
    exceededWorkloads.length > 0 ||
    tokenExceededWorkloads.length > 0;
  const hasData = config.exists || totalSeconds > 0 || ledgerFiles(dir, 'total').length > 0;
  const agents = Object.fromEntries(Object.entries(agentTotals).map(([name, totals]) => [name, {
    ...totals,
    totalTokens: totals.measuredTokens + totals.estimatedTokens,
  }]));

  return {
    dir,
    config: {
      version: config.version,
      execution_minutes: config.execution_minutes,
      period: config.period,
      workloads: config.workloads,
      tokens: config.tokens,
      allocation: config.allocation,
      // agents は台帳から毎回導出する（永続しない）。config.json 側に同名キーがあっても
      // 導出が正——観測より古い宣言で CLI を塞ぎ続けないため。
      computed: { ...config.computed, agents: quotaAgents },
      rates: config.rates,
    },
    knownWorkloads: KNOWN_WORKLOADS,
    // v1 互換フィールド
    totals: seconds,
    totalSeconds,
    totalRecords,
    totalUnestimatedRecords,
    limitSeconds,
    exceeded,
    exceededWorkloads,
    hasData,
    // v2 追加フィールド
    softRatio,
    tokenLimit,
    totalTokens: { measured: totalMeasured, estimated: totalEstimated, total: totalTokens },
    agents,
    tokenExceededTotal,
    timeExceededTotal,
    tokenExceededWorkloads,
    softWorkloads,
    workloads,
  };
}

function validateNonNeg(v, label) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) throw new Error(`${label} は 0 以上の数値で指定してください`);
  return n;
}

// allocation パッチの検証と正規化（既存 allocation へ深くマージする値を返す）。
function normalizeAllocationPatch(base, patch) {
  const out = isPlainObject(base) ? { ...base } : {};
  if (patch.mode !== undefined) {
    if (!ALLOC_MODES.includes(patch.mode)) {
      throw new Error(`allocation.mode が不正です: ${patch.mode}（static / auto）`);
    }
    out.mode = patch.mode;
  }
  if (patch.soft_ratio !== undefined) {
    const n = Number(patch.soft_ratio);
    if (!Number.isFinite(n) || n < 0 || n > 1) throw new Error('soft_ratio は 0〜1 で指定してください');
    out.soft_ratio = n;
  }
  if (patch.rebalance_interval_sec !== undefined) {
    out.rebalance_interval_sec = validateNonNeg(patch.rebalance_interval_sec, 'rebalance_interval_sec');
  }
  if (patch.workloads !== undefined) {
    if (!isPlainObject(patch.workloads)) throw new Error('allocation.workloads はオブジェクトで指定してください');
    const baseWl = isPlainObject(out.workloads) ? { ...out.workloads } : {};
    for (const [w, spec] of Object.entries(patch.workloads)) {
      if (!isPlainObject(spec)) throw new Error(`allocation.workloads.${w} はオブジェクトで指定してください`);
      const cur = isPlainObject(baseWl[w]) ? { ...baseWl[w] } : {};
      if (spec.weight !== undefined) cur.weight = validateNonNeg(spec.weight, `配分比（${w}）`);
      if (spec.min_tokens !== undefined) cur.min_tokens = validateNonNeg(spec.min_tokens, `下限（${w}）`);
      if (spec.max_tokens !== undefined) cur.max_tokens = validateNonNeg(spec.max_tokens, `上限（${w}）`);
      if (spec.on_exhausted !== undefined) {
        if (!ON_EXHAUSTED.includes(spec.on_exhausted)) {
          throw new Error(`on_exhausted が不正です（${w}）: ${spec.on_exhausted}（pause / stop / degrade）`);
        }
        cur.on_exhausted = spec.on_exhausted;
      }
      baseWl[w] = cur;
    }
    out.workloads = baseWl;
  }
  return out;
}

// 上限・期間・トークン上限・配分の保存（部分更新）。0 = 無制限。version:2 で書く。
// 書くのは config.json だけ（台帳は各エンジンの所有）。原子書換（tmp → rename）。
function save(cfg, patch) {
  const dir = resolveBudgetDir(cfg);
  const cur = loadBudgetConfig(dir);
  const p = patch || {};
  const next = { ...cur.raw }; // additive: 未知キーを保持
  next.version = 2;
  next.execution_minutes = cur.execution_minutes;
  next.period = cur.period;
  next.workloads = { ...cur.workloads };
  next.tokens = cur.tokens;
  next.allocation = isPlainObject(cur.allocation) ? { ...cur.allocation } : {};

  if (p.executionMinutes !== undefined) {
    next.execution_minutes = validateNonNeg(p.executionMinutes, '合計上限（分）');
  }
  if (p.period !== undefined) {
    if (!['day', 'month', 'total'].includes(p.period)) {
      throw new Error(`period が不正です: ${p.period}（day / month / total）`);
    }
    next.period = p.period;
  }
  if (p.workloads && typeof p.workloads === 'object') {
    for (const [wl, v] of Object.entries(p.workloads)) {
      next.workloads[wl] = validateNonNeg(v, `内訳上限（${wl}）`);
    }
  }
  if (p.tokens !== undefined) {
    next.tokens = validateNonNeg(p.tokens, 'トークン上限');
  }
  if (p.allocation !== undefined) {
    if (!isPlainObject(p.allocation)) throw new Error('allocation はオブジェクトで指定してください');
    next.allocation = normalizeAllocationPatch(next.allocation, p.allocation);
  }
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'config.json'), next);
  return usage(cfg);
}

function clamp(value, min, max) {
  let v = value;
  if (min > 0 && v < min) v = min;
  if (max > 0 && v > max) v = max;
  return v;
}

// アロケータ: 残り枠 R を weight 比で配分し computed.workloads.<w>.tokens を書く。
// R      = max(0, tokens − Σ 全消費トークン)
// cap_w  = clamp(consumed_w + R × weight_w / Σ weight_active, min_tokens_w, max_tokens_w)
// これは手動トリガ（IPC orchestration:rebalance）であり mode に関わらず走る。auto の場合は
// 管理面が rebalance_interval_sec ごとに同じ計算を回す（エンジン側の判定は従来どおり単純比較）。
function rebalance(cfg) {
  const dir = resolveBudgetDir(cfg);
  const config = loadBudgetConfig(dir);
  const u = usage(cfg);
  const consumedByWl = {};
  for (const [wl, w] of Object.entries(u.workloads)) consumedByWl[wl] = w.totalTokens || 0;
  const totalConsumed = u.totalTokens.total;
  const R = Math.max(0, config.tokens - totalConsumed);

  const allocWl = isPlainObject(config.allocation.workloads) ? config.allocation.workloads : {};
  const active = [];
  for (const [w, spec] of Object.entries(allocWl)) {
    const weight = spec && spec.weight !== undefined ? Number(spec.weight) : 1;
    if (Number.isFinite(weight) && weight > 0) active.push([w, weight, spec || {}]);
  }
  const sumW = active.reduce((s, [, w]) => s + w, 0);

  const computedWl = {};
  for (const [w, weight, spec] of active) {
    const consumed = consumedByWl[w] || 0;
    const share = sumW > 0 ? (R * weight) / sumW : 0;
    const min = Math.max(0, Number(spec.min_tokens) || 0);
    const max = Math.max(0, Number(spec.max_tokens) || 0);
    computedWl[w] = { tokens: Math.round(clamp(consumed + share, min, max)) };
  }

  const next = { ...config.raw };
  next.version = 2;
  next.computed = {
    ...(isPlainObject(config.computed) ? config.computed : {}),
    workloads: computedWl,
    computed_at: nowStamp(),
    computed_by: 'dashboard',
  };
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'config.json'), next);
  return usage(cfg);
}

function median(nums) {
  const arr = nums.slice().sort((a, b) => a - b);
  const n = arr.length;
  if (!n) return 0;
  const mid = Math.floor(n / 2);
  return n % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

// レート較正: seconds と実測トークンが両方ある行から (agent_cli, model) ごとの
// 実効 tokens/秒（外れ値に強い中央値）を求め、config.rates.per_cli へ書き戻す。
// キーは model があれば "cli:model"、無ければ "cli"。エンジンは較正を知らずレート表を読むだけ。
function calibrateRates(cfg) {
  const dir = resolveBudgetDir(cfg);
  const config = loadBudgetConfig(dir);
  const samples = {};
  for (const rec of ledgerRecords(dir, 'total')) {
    const sec = Number(rec.seconds);
    if (!(Number.isFinite(sec) && sec > 0)) continue;
    if (!isMeasured(rec)) continue;
    const tot = Math.max(0, Number(rec.tokens_in) || 0) + Math.max(0, Number(rec.tokens_out) || 0);
    if (!(tot > 0)) continue;
    const cli = rec.agent_cli ? String(rec.agent_cli) : '';
    if (!cli) continue;
    const model = rec.model !== undefined && rec.model !== null && String(rec.model) !== '' ? String(rec.model) : '';
    const key = model ? `${cli}:${model}` : cli;
    (samples[key] || (samples[key] = [])).push(tot / sec);
  }
  const perCli = isPlainObject(config.rates.per_cli) ? { ...config.rates.per_cli } : {};
  for (const [k, arr] of Object.entries(samples)) perCli[k] = median(arr);
  const nextRates = { ...(isPlainObject(config.rates) ? config.rates : {}), per_cli: perCli };

  const next = { ...config.raw };
  next.version = 2;
  next.rates = nextRates;
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, 'config.json'), next);
  return nextRates;
}

module.exports = {
  resolveBudgetDir,
  loadBudgetConfig,
  ledgerRecords,
  quotaAgentsFrom,
  QUOTA_RATE_LIMIT_TTL_SEC,
  rate,
  rowTokens,
  usage,
  save,
  rebalance,
  calibrateRates,
  KNOWN_WORKLOADS,
};
