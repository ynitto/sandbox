'use strict';

// 実行プロファイル自動選択（agent-profiles 契約）。
// 正典: schemas/agent-profiles.schema.json。実体は $AGENT_CONTROL_DIR（既定 ~/.agents/control/）の
// profiles.json。**不変条件: この契約はエンジンから読まれない**——dashboard がワークロードの
// 予算残率（node-budget）と agent CLI ごとの枠（node-budget の allocation.agents）から段
// （単純作業/軽量/標準/高性能）と候補（agent_cli+model）を決定的に選び、選択結果だけを agent-control（control.json）
// へ投函する。エンジン側の解決経路は増やさない（柱1 / C2・C7）。
//
// decide() は純関数（時刻・budget usage・profiles 設定だけを引数に取る。ファイル I/O も
// Date.now() / Math.random() も持たない）。副作用（読み書き）は load / save / apply に閉じる。
//
// 設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §1

const fs = require('fs');
const path = require('path');
const budget = require('./budget');
const control = require('./control');

const PROFILES_FILE = 'profiles.json';

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

function atomicWriteJson(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(obj, null, 2)}\n`);
  fs.renameSync(tmp, target);
}

function nowStamp() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

// profiles.json は control.json と同じディレクトリに置く（$AGENT_CONTROL_DIR）。
function resolveProfilesDir(cfg) {
  return control.resolveControlDir(cfg);
}

function clamp01(n, dflt) {
  const v = Number(n);
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : dflt;
}

function normalizeCandidate(raw) {
  if (!isPlainObject(raw)) return null;
  const out = {};
  if (typeof raw.agent_cli === 'string' && raw.agent_cli.trim()) out.agent_cli = raw.agent_cli.trim();
  if (typeof raw.model === 'string' && raw.model.trim()) out.model = raw.model.trim();
  return out.agent_cli || out.model ? out : null;
}

function normalizeTiers(raw) {
  const out = {};
  if (!isPlainObject(raw)) return out;
  for (const [name, spec] of Object.entries(raw)) {
    if (!isPlainObject(spec)) continue;
    const order = Number(spec.order);
    if (!Number.isFinite(order)) continue;
    const candidates = Array.isArray(spec.candidates)
      ? spec.candidates.map(normalizeCandidate).filter(Boolean)
      : [];
    out[name] = { order, label: typeof spec.label === 'string' && spec.label ? spec.label : name, candidates };
  }
  return out;
}

function normalizePolicy(raw) {
  const p = isPlainObject(raw) ? raw : {};
  const steps = Array.isArray(p.steps)
    ? p.steps
        .map((s) =>
          isPlainObject(s) && Number.isFinite(Number(s.min_remaining_ratio)) && typeof s.tier === 'string' && s.tier
            ? { min_remaining_ratio: clamp01(s.min_remaining_ratio, 0), tier: s.tier }
            : null
        )
        .filter(Boolean)
    : [];
  const minHold = Number(p.min_hold_sec);
  const interval = Number(p.interval_sec);
  return {
    apply_to: Array.isArray(p.apply_to) ? p.apply_to.filter((x) => typeof x === 'string' && x) : [],
    steps,
    no_cap_tier: typeof p.no_cap_tier === 'string' ? p.no_cap_tier : '',
    hysteresis: clamp01(p.hysteresis, 0.05),
    min_hold_sec: Number.isFinite(minHold) && minHold >= 0 ? minHold : 900,
    interval_sec: Number.isFinite(interval) && interval >= 0 ? interval : 300,
  };
}

function normalizeState(raw) {
  const out = {};
  if (!isPlainObject(raw)) return out;
  for (const [wl, rec] of Object.entries(raw)) {
    if (!isPlainObject(rec)) continue;
    out[wl] = {
      tier: typeof rec.tier === 'string' ? rec.tier : '',
      candidate: normalizeCandidate(rec.candidate),
      since: typeof rec.since === 'string' ? rec.since : '',
      reason: typeof rec.reason === 'string' ? rec.reason : '',
    };
  }
  return out;
}

function normalizeExecutionPolicy(raw) {
  if (!isPlainObject(raw) || !['auto', 'saving', 'quality', 'custom'].includes(raw.mode)) return null;
  return {
    mode: raw.mode,
    custom: isPlainObject(raw.custom) ? raw.custom : {},
  };
}

function defaultProfiles() {
  return { version: 1, enabled: true, tiers: {}, policy: normalizePolicy({}), state: {}, executionPolicy: null };
}

function loadProfiles(dir) {
  const file = path.join(dir, PROFILES_FILE);
  const raw = readJson(file);
  if (!isPlainObject(raw)) {
    return { ...defaultProfiles(), exists: false, raw: {} };
  }
  return {
    version: 1,
    enabled: raw.enabled !== false,
    tiers: normalizeTiers(raw.tiers),
    policy: normalizePolicy(raw.policy),
    state: normalizeState(raw.state),
    executionPolicy: normalizeExecutionPolicy(raw.execution_policy),
    exists: true,
    raw,
  };
}

function load(cfg) {
  return loadProfiles(resolveProfilesDir(cfg));
}

// tiers / policy の宣言を保存する（部分更新。policy はフィールド単位でマージ——interval_sec
// のように画面が表示しないフィールドを、他フィールドの保存で消さないため）。
// state は save() では触らない（state は apply() が書く導出結果——「人が編集する場」と
// 「機械が記録する場」は同じファイルの別セクションとして扱い、書き手を混ぜない）。
function save(cfg, patch) {
  const dir = resolveProfilesDir(cfg);
  const cur = loadProfiles(dir);
  const p = patch || {};
  const next = { ...(cur.raw || {}) };
  next.version = 1;
  next.enabled = p.enabled !== undefined ? Boolean(p.enabled) : cur.enabled;
  if (p.tiers !== undefined) {
    if (!isPlainObject(p.tiers)) throw new Error('tiers はオブジェクトで指定してください');
    next.tiers = normalizeTiers(p.tiers);
  } else {
    next.tiers = cur.tiers;
  }
  if (p.policy !== undefined) {
    if (!isPlainObject(p.policy)) throw new Error('policy はオブジェクトで指定してください');
    next.policy = normalizePolicy({ ...cur.policy, ...p.policy });
  } else {
    next.policy = cur.policy;
  }
  next.state = cur.state;
  if (p.executionPolicy !== undefined) {
    const executionPolicy = normalizeExecutionPolicy(p.executionPolicy);
    if (!executionPolicy) throw new Error('executionPolicy.mode が不正です');
    next.execution_policy = executionPolicy;
  }
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, PROFILES_FILE), next);
  return loadProfiles(dir);
}

// --- 決定ロジック（純関数） -------------------------------------------------

function orderedTierNames(tiers) {
  return Object.entries(tiers)
    .sort((a, b) => b[1].order - a[1].order)
    .map(([name]) => name);
}

function pickTierFromSteps(steps, remaining) {
  const sorted = steps.slice().sort((a, b) => b.min_remaining_ratio - a.min_remaining_ratio);
  for (const s of sorted) {
    if (remaining >= s.min_remaining_ratio) return { tier: s.tier, threshold: s.min_remaining_ratio };
  }
  return null;
}

function agentHasRoom(allocationAgents, usageAgents, computedAgents, cli, nowMs) {
  const quota = isPlainObject(computedAgents) ? computedAgents[cli] : null;
  if (isPlainObject(quota)) {
    // exhausted は「期間が変わるまで戻らない枠」。観測は period 窓の台帳から導出されるので、
    // 期間が変わればこのエントリ自体が消える（ここで失効時刻を持たなくてよい）。
    if (quota.quota_kind === 'exhausted') return false;
    // rate_limit は復帰時刻まで塞ぐ。**時刻が読めないときは塞がない** — 塞ぎっぱなしにすると
    // 「止めないための機能」が「二度と使えない」を作る。導出側（budget.quotaAgentsFrom）が
    // 既定 TTL を埋めるので、ここへ来る時点で読めないのは判断材料が無い場合だけ。
    if (quota.quota_kind === 'rate_limit') {
      const resetMs = Date.parse(quota.reset_at || '');
      if (Number.isFinite(resetMs) && nowMs < resetMs) return false;
    }
  }
  const alloc = isPlainObject(allocationAgents) ? allocationAgents[cli] : null;
  const maxTokens = alloc && Number.isFinite(Number(alloc.max_tokens)) ? Number(alloc.max_tokens) : 0;
  if (!(maxTokens > 0)) return true; // 未設定 = 常に残っている
  const used = (usageAgents && usageAgents[cli] && Number(usageAgents[cli].totalTokens)) || 0;
  return used < maxTokens;
}

// tier（budget が決めた段）の候補列から、枠が残っている最初の候補を探す。
// 全滅なら一段下（次に order が低い段）へ降りて続ける。見つからなければ null。
function pickCandidate(tiers, startTier, allocationAgents, usageAgents, computedAgents, nowMs) {
  const order = orderedTierNames(tiers);
  const startIdx = order.indexOf(startTier);
  if (startIdx < 0) return null;
  for (let i = startIdx; i < order.length; i += 1) {
    const name = order[i];
    const spec = tiers[name];
    for (const cand of spec.candidates) {
      if (!cand.agent_cli || agentHasRoom(
        allocationAgents, usageAgents, computedAgents, cand.agent_cli, nowMs
      )) {
        return { fromTier: name, candidate: cand, fellBack: i > startIdx };
      }
    }
  }
  return null;
}

// ワークフローのノードは tier を明示しているため、同じ tier 内だけで候補を選ぶ。
// 自動プロファイルのような下位 tier への降格は、実行品質を変えるので行わない。
function candidateForTier(tiers, tier, usage, nowMs) {
  const spec = tiers && tiers[tier];
  if (!spec) return null;
  const allocationAgents =
    (usage && usage.config && isPlainObject(usage.config.allocation) && usage.config.allocation.agents) || {};
  const usageAgents = (usage && usage.agents) || {};
  const computedAgents =
    (usage && usage.config && isPlainObject(usage.config.computed) && usage.config.computed.agents) || {};
  for (const candidate of spec.candidates || []) {
    if (!candidate.agent_cli || agentHasRoom(
      allocationAgents, usageAgents, computedAgents, candidate.agent_cli, nowMs
    )) return { ...candidate };
  }
  return null;
}

function resolveTier(cfg, tier) {
  return candidateForTier(load(cfg).tiers, tier, budget.usage(cfg), Date.now());
}

// 1 ワークロード分の決定。tier は budget（予算残率・ヒステリシス・最小保持）だけで決め、
// quota（CLI 枠）で候補が下の段へフォールバックした場合は、その実際の段を state に残す。
// reset 後の上位復帰は、この state を通じて既存のヒステリシスと最小保持が効く。
function decideOne({
  tiers, policy, usageWorkload, usageAgents, allocationAgents, computedAgents, prevState, nowMs,
}) {
  const cap = usageWorkload ? Number(usageWorkload.tokenCap) || 0 : 0;
  const totalTokens = usageWorkload ? Number(usageWorkload.totalTokens) || 0 : 0;

  let tier0 = null;
  let remaining = null;
  let stepThreshold = null;
  if (!(cap > 0)) {
    if (policy.no_cap_tier && tiers[policy.no_cap_tier]) {
      tier0 = policy.no_cap_tier;
      remaining = 1;
    }
  } else {
    remaining = Math.max(0, 1 - totalTokens / cap);
    const picked = pickTierFromSteps(policy.steps, remaining);
    if (picked) {
      tier0 = picked.tier;
      stepThreshold = picked.threshold;
    }
  }
  if (!tier0 || !tiers[tier0]) return null; // 決められない（宣言不足）

  const prevTier = prevState && prevState.tier && tiers[prevState.tier] ? prevState.tier : null;
  const prevOrder = prevTier ? tiers[prevTier].order : null;
  const tier0Order = tiers[tier0].order;

  // ヒステリシス: 上位へ戻ろうとしているときだけ上乗せを要求する（下降は素直に効かせる）。
  let afterHysteresis = tier0;
  if (prevTier && tier0Order > prevOrder) {
    const required = (stepThreshold !== null ? stepThreshold : 0) + policy.hysteresis;
    if (remaining === null || remaining < required) afterHysteresis = prevTier;
  }

  // 最小保持: 前回の段からこの秒数は動かさない（上昇・下降とも）。
  let finalTier = afterHysteresis;
  let heldByMinHold = false;
  const prevSinceMs = prevState && prevState.since ? Date.parse(prevState.since) : NaN;
  if (
    prevTier &&
    afterHysteresis !== prevTier &&
    Number.isFinite(prevSinceMs) &&
    nowMs - prevSinceMs < policy.min_hold_sec * 1000
  ) {
    finalTier = prevTier;
    heldByMinHold = true;
  }

  const picked = pickCandidate(
    tiers, finalTier, allocationAgents, usageAgents, computedAgents, nowMs
  );
  const remainingText = remaining === null ? 'unlimited' : remaining.toFixed(2);
  const parts = [`remaining=${remainingText}`, `tier=${finalTier}`];
  if (heldByMinHold) parts.push('min-hold');
  else if (afterHysteresis !== tier0) parts.push('hysteresis-hold');

  if (!picked) {
    return { tier: finalTier, candidate: null, reason: `${parts.join(' ')} 候補の枠がすべて枯渇` };
  }
  if (picked.fellBack) {
    parts[1] = `tier=${picked.fromTier}`;
    parts.push(`quota-fallback→${picked.fromTier}`);
  }
  return { tier: picked.fromTier, candidate: picked.candidate, reason: parts.join(' ') };
}

function sameCandidate(a, b) {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.agent_cli === b.agent_cli && a.model === b.model;
}

// decide(profiles, usage, nowMs) -> { [workload]: {tier, candidate, reason, changed, since} }
// 純関数。usage は budget.usage() の戻り値をそのまま渡す。nowMs は呼び出し側が確定する
// （Date.now() をここで呼ばない——同じ入力なら同じ出力であることをテストで固定するため）。
function decide(profiles, usage, nowMs) {
  const out = {};
  if (!profiles || profiles.enabled === false) return out;
  const tiers = profiles.tiers || {};
  const policy = profiles.policy || {};
  const state = profiles.state || {};
  const allocationAgents =
    (usage && usage.config && isPlainObject(usage.config.allocation) && usage.config.allocation.agents) || {};
  const usageAgents = (usage && usage.agents) || {};
  const computedAgents =
    (usage && usage.config && isPlainObject(usage.config.computed) && usage.config.computed.agents) || {};
  for (const wl of policy.apply_to || []) {
    const usageWorkload = usage && usage.workloads ? usage.workloads[wl] : null;
    const prevState = state[wl] || null;
    const result = decideOne({
      tiers, policy, usageWorkload, usageAgents, allocationAgents, computedAgents, prevState, nowMs,
    });
    if (!result) continue;
    const tierChanged = !prevState || prevState.tier !== result.tier;
    out[wl] = {
      tier: result.tier,
      candidate: result.candidate,
      reason: result.reason,
      changed: tierChanged || !sameCandidate(prevState && prevState.candidate, result.candidate),
      since: tierChanged || !prevState || !prevState.since ? new Date(nowMs).toISOString() : prevState.since,
    };
  }
  return out;
}

// --- 適用（副作用はここに閉じる） -------------------------------------------

// 書かずに決定だけ見せる（画面の dry-run ボタン用）。
function evaluate(cfg) {
  const dir = resolveProfilesDir(cfg);
  const profiles = loadProfiles(dir);
  const usage = budget.usage(cfg);
  const decisions = decide(profiles, usage, Date.now());
  return { profiles, usage, decisions };
}

// 決定を control.json（選択結果）と profiles.json（state=記録）へ書く。
// 現状と一致する決定は書かない（saveControl は必ず revision++ するため、無変化の書き込みは
// revision_applied 突き合わせを無意味にする）。
function apply(cfg) {
  const dir = resolveProfilesDir(cfg);
  const { profiles, decisions } = evaluate(cfg);
  const controlDir = control.resolveControlDir(cfg);
  const curControl = control.loadControl(controlDir);

  const controlPatch = {};
  const nextState = { ...profiles.state };
  let stateDirty = false;

  for (const [wl, decision] of Object.entries(decisions)) {
    const curWl = curControl.workloads[wl] || {};
    // 候補まで決まったときだけ control を触る（候補が全滅した段は「決められなかった」
    // ので、段だけ書き替えると実際に走る CLI と段の申告がずれる）。
    if (decision.candidate) {
      const patch = {};
      const curCandidate = { agent_cli: curWl.agent_cli || undefined, model: curWl.model || undefined };
      if (!sameCandidate(curCandidate, decision.candidate)) {
        patch.agent_cli = decision.candidate.agent_cli || null;
        patch.model = decision.candidate.model || null;
      }
      // 段そのものも control へ運ぶ。エンジン（手法パックの `when.tiers`）が読むのは
      // agent-control だけで、この契約（profiles.json）は読まれない——不変条件を保つ。
      if (String(curWl.tier || '') !== String(decision.tier || '')) {
        patch.tier = decision.tier || null;
      }
      if (curWl.selection_source !== 'control-workload') patch.selection_source = 'control-workload';
      if (curWl.selection_reason !== decision.reason) patch.selection_reason = decision.reason;
      if (curWl.pinned !== false) patch.pinned = false;
      if (Object.keys(patch).length > 0) controlPatch[wl] = patch;
    }
    const prev = profiles.state[wl];
    if (
      !prev ||
      prev.tier !== decision.tier ||
      prev.reason !== decision.reason ||
      !sameCandidate(prev.candidate, decision.candidate)
    ) {
      nextState[wl] = { tier: decision.tier, candidate: decision.candidate, since: decision.since, reason: decision.reason };
      stateDirty = true;
    }
  }

  if (Object.keys(controlPatch).length > 0) {
    control.saveControl(cfg, { workloads: controlPatch });
  }
  if (stateDirty) {
    const next = { ...(profiles.raw || {}) };
    next.version = 1;
    next.state = nextState;
    next.updated_at = nowStamp();
    next.updated_by = 'dashboard';
    atomicWriteJson(path.join(dir, PROFILES_FILE), next);
  }
  return { decisions, controlWritten: Object.keys(controlPatch).length > 0, stateWritten: stateDirty };
}

module.exports = {
  resolveProfilesDir,
  loadProfiles,
  load,
  save,
  decide,
  candidateForTier,
  resolveTier,
  evaluate,
  apply,
};
