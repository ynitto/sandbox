'use strict';

// 実行プロファイル自動選択（agent-profiles 契約）。
// 正典: schemas/agent-profiles.schema.json。実体は $AGENT_CONTROL_DIR（既定 ~/.agents/control/）の
// profiles.json。**不変条件: この契約はエンジンから読まれない**——dashboard がワークロードの
// 予算残率と、agent-auditがnode-budget台帳へ書いたCLI quotaから段
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
const agents = require('./agents');
const qualifications = require('./qualifications');
const policyCompiler = require('./execution-policy-compiler');

const PROFILES_FILE = 'profiles.json';
const METERED_CLIS = new Set(['claude', 'codex', 'copilot', 'kiro']);
const QUOTA_ORANGE_PERCENT = 70;
const QUOTA_RED_PERCENT = 90;
const QUOTA_MIN_STALE_SEC = 900;

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

function withoutVariantTargets(tiers, targets) {
  const removed = [];
  const sanitized = {};
  for (const [tier, spec] of Object.entries(tiers || {})) {
    const candidates = (spec.candidates || []).filter((candidate) => {
      const cli = String(candidate.agent_cli || '').trim().toLowerCase();
      if (!cli || !targets.has(cli)) return true;
      removed.push({ tier, ...candidate });
      return false;
    });
    sanitized[tier] = { ...spec, candidates };
  }
  return { tiers: removed.length ? sanitized : tiers, removed };
}

function selectableProfile(cfg, profile) {
  const filtered = withoutVariantTargets(profile.tiers, agents.variantTargetNames(cfg));
  return { ...profile, tiers: filtered.tiers, removedVariantCandidates: filtered.removed };
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
    const normalized = {
      tier: typeof rec.tier === 'string' ? rec.tier : '',
      candidate: normalizeCandidate(rec.candidate),
      since: typeof rec.since === 'string' ? rec.since : '',
      reason: typeof rec.reason === 'string' ? rec.reason : '',
    };
    if (typeof rec.target_tier === 'string') normalized.target_tier = rec.target_tier;
    if (typeof rec.target_since === 'string') normalized.target_since = rec.target_since;
    out[wl] = normalized;
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

function missingPolicyTiers(tiers, policy) {
  const names = new Set([
    policy.no_cap_tier,
    ...(policy.steps || []).map((step) => step.tier),
  ].filter(Boolean));
  return [...names].filter((name) => !tiers[name] || !tiers[name].candidates.length);
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
  return selectableProfile(cfg, loadProfiles(resolveProfilesDir(cfg)));
}

// tiers / policy の宣言を保存する（部分更新。policy はフィールド単位でマージ——interval_sec
// のように画面が表示しないフィールドを、他フィールドの保存で消さないため）。
// state は save() では触らない（state は apply() が書く導出結果——「人が編集する場」と
// 「機械が記録する場」は同じファイルの別セクションとして扱い、書き手を混ぜない）。
function save(cfg, patch) {
  const dir = resolveProfilesDir(cfg);
  const cur = loadProfiles(dir);
  const p = patch || {};
  const variantTargets = agents.variantTargetNames(cfg);
  const next = { ...(cur.raw || {}) };
  next.version = 1;
  next.enabled = p.enabled !== undefined ? Boolean(p.enabled) : cur.enabled;
  if (p.tiers !== undefined) {
    if (!isPlainObject(p.tiers)) throw new Error('tiers はオブジェクトで指定してください');
    const normalized = normalizeTiers(p.tiers);
    const invalid = withoutVariantTargets(normalized, variantTargets).removed;
    if (invalid.length) {
      const names = [...new Set(invalid.map((candidate) => candidate.agent_cli))];
      throw new Error(`variant（変種）先は汎用tier候補に指定できません: ${names.join(', ')}`);
    }
    next.tiers = normalized;
  } else {
    // 旧版が保存した variant 直接候補は、他項目の保存時にも移行する。
    next.tiers = withoutVariantTargets(cur.tiers, variantTargets).tiers;
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
  const missing = missingPolicyTiers(next.tiers, next.policy);
  if (missing.length) {
    throw new Error(`方針で使う実行レベルに候補がありません: ${missing.join(', ')}`);
  }
  next.updated_at = nowStamp();
  next.updated_by = 'dashboard';
  atomicWriteJson(path.join(dir, PROFILES_FILE), next);
  return load(cfg);
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

function quotaBand(computedAgents, cli, nowMs, intervalSec) {
  const quota = isPlainObject(computedAgents) ? computedAgents[cli] : null;
  if (!cli) return { name: 'green', rank: 0, usedPercent: null };
  if (!isPlainObject(quota)) {
    return METERED_CLIS.has(cli)
      ? { name: 'unknown', rank: 2, usedPercent: null }
      : { name: 'green', rank: 0, usedPercent: null };
  }
  const observedMs = Date.parse(quota.observed_at || '');
  const resetMs = Date.parse(quota.reset_at || '');
  const staleMs = Math.max(QUOTA_MIN_STALE_SEC, Math.max(0, Number(intervalSec) || 0) * 3) * 1000;
  if (!Number.isFinite(observedMs) || nowMs - observedMs > staleMs
      || (Number.isFinite(resetMs) && observedMs < resetMs && resetMs <= nowMs)) {
    return { name: 'unknown', rank: 2, usedPercent: null };
  }
  const used = Number(quota.quota_used_percent);
  if (!Number.isFinite(used)) return { name: 'unknown', rank: 2, usedPercent: null };
  if (used >= 100) return { name: 'blocked', rank: Infinity, usedPercent: used };
  if (used >= QUOTA_RED_PERCENT) return { name: 'red', rank: 3, usedPercent: used };
  if (used >= QUOTA_ORANGE_PERCENT) return { name: 'orange', rank: 1, usedPercent: used };
  return { name: 'green', rank: 0, usedPercent: used };
}

function bestCandidate(spec, allocationAgents, usageAgents, computedAgents, nowMs, intervalSec, current) {
  const ranked = [];
  for (let index = 0; index < (spec.candidates || []).length; index += 1) {
    const candidate = spec.candidates[index];
    if (candidate.agent_cli && !agentHasRoom(
      allocationAgents, usageAgents, computedAgents, candidate.agent_cli, nowMs
    )) continue;
    const band = quotaBand(computedAgents, candidate.agent_cli, nowMs, intervalSec);
    if (band.name === 'blocked') continue;
    ranked.push({ candidate, band, index, current: sameCandidate(candidate, current) ? 0 : 1 });
  }
  ranked.sort((a, b) => a.band.rank - b.band.rank || a.current - b.current || a.index - b.index);
  return ranked[0] || null;
}

// tier内は quota帯→現在候補→宣言順で選ぶ。全候補が赤なら、緑/オレンジへ改善する
// 下位tierだけへ降格する。hard block時は下位の非block候補を使い、全滅ならnull。
function pickCandidate(
  tiers, startTier, allocationAgents, usageAgents, computedAgents, nowMs, intervalSec, current
) {
  const order = orderedTierNames(tiers);
  const startIdx = order.indexOf(startTier);
  if (startIdx < 0) return null;
  const first = bestCandidate(
    tiers[startTier], allocationAgents, usageAgents, computedAgents, nowMs, intervalSec, current
  );
  if (first && first.band.name !== 'red') {
    return { fromTier: startTier, candidate: first.candidate, band: first.band, fellBack: false };
  }
  for (let i = first ? startIdx + 1 : startIdx; i < order.length; i += 1) {
    const name = order[i];
    const spec = tiers[name];
    const picked = bestCandidate(
      spec, allocationAgents, usageAgents, computedAgents, nowMs, intervalSec, current
    );
    if (!picked) continue;
    // 赤からtierを下げるのは、下位候補が緑/オレンジでquotaが実際に改善するときだけ。
    if (first && first.band.name === 'red' && i > startIdx && picked.band.rank > 1) continue;
    return { fromTier: name, candidate: picked.candidate, band: picked.band, fellBack: i > startIdx };
  }
  if (first) return { fromTier: startTier, candidate: first.candidate, band: first.band, fellBack: false };
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
  const picked = bestCandidate(spec, allocationAgents, usageAgents, computedAgents, nowMs, 300, null);
  return picked ? { ...picked.candidate } : null;
}

function resolveTier(cfg, tier) {
  return candidateForTier(load(cfg).tiers, tier, budget.usage(cfg), Date.now());
}

// 1ワークロード分の決定。予算が決めるtargetTierと、quota反映後の実効tierを分離する。
// quota復帰は80%未満・最小保持経過後にtargetTierへ一段ずつ戻す。
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

  const previousTarget = prevState && (prevState.target_tier || prevState.tier);
  const prevTier = previousTarget && tiers[previousTarget] ? previousTarget : null;
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
  const previousTargetSince = prevState && (prevState.target_since || prevState.since);
  const prevSinceMs = previousTargetSince ? Date.parse(previousTargetSince) : NaN;
  if (
    prevTier &&
    afterHysteresis !== prevTier &&
    Number.isFinite(prevSinceMs) &&
    nowMs - prevSinceMs < policy.min_hold_sec * 1000
  ) {
    finalTier = prevTier;
    heldByMinHold = true;
  }

  // quotaで下位tierへ退避している間の復帰は、予算targetの昇格とは別に扱う。
  // 90%で降格した候補が80%未満へ戻り、実効tierの保持時間を過ぎたときだけ一段戻す。
  let selectionTier = finalTier;
  let quotaRecoveryHeld = false;
  const prevEffective = prevState && prevState.tier && tiers[prevState.tier] ? prevState.tier : null;
  if (prevEffective && previousTarget === finalTier
      && tiers[prevEffective].order < tiers[finalTier].order) {
    const nextTier = Object.entries(tiers)
      .filter(([, spec]) => spec.order > tiers[prevEffective].order && spec.order <= tiers[finalTier].order)
      .sort((a, b) => a[1].order - b[1].order)[0];
    const effectiveSinceMs = Date.parse((prevState && prevState.since) || '');
    const holdElapsed = !Number.isFinite(effectiveSinceMs)
      || nowMs - effectiveSinceMs >= policy.min_hold_sec * 1000;
    const recovery = nextTier && bestCandidate(
      nextTier[1], allocationAgents, usageAgents, computedAgents, nowMs, policy.interval_sec, null
    );
    const recoveryReady = recovery && recovery.band.name !== 'unknown'
      && recovery.band.name !== 'red'
      && (recovery.band.usedPercent === null || recovery.band.usedPercent < 80);
    if (holdElapsed && recoveryReady) selectionTier = nextTier[0];
    else {
      selectionTier = prevEffective;
      quotaRecoveryHeld = true;
    }
  }

  const picked = pickCandidate(
    tiers, selectionTier, allocationAgents, usageAgents, computedAgents, nowMs,
    policy.interval_sec, prevState && prevState.candidate
  );
  const remainingText = remaining === null ? 'unlimited' : remaining.toFixed(2);
  const parts = [`remaining=${remainingText}`, `tier=${finalTier}`];
  if (heldByMinHold) parts.push('min-hold');
  else if (afterHysteresis !== tier0) parts.push('hysteresis-hold');
  if (quotaRecoveryHeld) parts.push('quota-recovery-hold');

  if (!picked) {
    return {
      targetTier: finalTier, tier: finalTier, candidate: null,
      reason: `${parts.join(' ')} 候補の枠がすべて枯渇`,
    };
  }
  if (picked.fellBack) {
    parts[1] = `tier=${picked.fromTier}`;
    parts.push(`quota-fallback→${picked.fromTier}`);
  }
  if (picked.candidate.agent_cli && picked.band
      && (METERED_CLIS.has(picked.candidate.agent_cli)
        || isPlainObject(computedAgents[picked.candidate.agent_cli]))) {
    const percent = picked.band.usedPercent === null ? '' : `:${picked.band.usedPercent}%`;
    parts.push(`quota=${picked.candidate.agent_cli}:${picked.band.name}${percent}`);
  }
  return {
    targetTier: finalTier, tier: picked.fromTier, candidate: picked.candidate, reason: parts.join(' '),
  };
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
    const prevTargetTier = prevState && (prevState.target_tier || prevState.tier);
    const targetChanged = !prevState || prevTargetTier !== result.targetTier;
    const tierChanged = !prevState || prevState.tier !== result.tier;
    out[wl] = {
      targetTier: result.targetTier,
      targetSince: targetChanged || !prevState || !prevState.target_since
        ? new Date(nowMs).toISOString() : prevState.target_since,
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
function evaluate(cfg, { force = false, nowMs = Date.now() } = {}) {
  const profiles = load(cfg);
  const usage = budget.usage(cfg);
  const decisions = decide(force ? { ...profiles, state: {} } : profiles, usage, nowMs);
  return { profiles, usage, decisions, nowMs };
}

function strategyOf(executionPolicy) {
  const mode = executionPolicy && executionPolicy.mode;
  if (mode === 'saving') return 'economy';
  if (mode === 'quality') return 'quality';
  if (mode === 'custom') {
    const strategy = executionPolicy.custom && executionPolicy.custom.strategy;
    return policyCompiler.STRATEGIES.includes(strategy) ? strategy : 'balanced';
  }
  return 'balanced';
}

// 決定を control.json（選択結果）と profiles.json（state=記録）へ書く。
// 現状と一致する決定は書かない（saveControl は必ず revision++ するため、無変化の書き込みは
// revision_applied 突き合わせを無意味にする）。
function apply(cfg, options) {
  const dir = resolveProfilesDir(cfg);
  const { profiles, decisions, nowMs } = evaluate(cfg, options);
  const controlDir = control.resolveControlDir(cfg);
  const curControl = control.loadControl(controlDir);
  const qualificationDoc = qualifications.load(cfg);

  const controlPatch = {};
  const nextState = { ...profiles.state };
  let stateDirty = false;
  let compiledAny = false;

  for (const [wl, decision] of Object.entries(decisions)) {
    const curWl = curControl.workloads[wl] || {};
    if (!decision.candidate) {
      const lifecycleOwnedByQuota = !curWl.lifecycle || curWl.lifecycle === 'run'
        || curWl.lifecycle_source === 'quota';
      if (lifecycleOwnedByQuota && (curWl.lifecycle !== 'pause' || curWl.lifecycle_source !== 'quota'
          || curWl.selection_reason !== decision.reason)) {
        controlPatch[wl] = {
          lifecycle: 'pause', lifecycle_source: 'quota', selection_reason: decision.reason,
        };
      }
    }
    // 候補まで決まったときだけtier/CLIを触る。全滅時は古い候補を残すが、quota所有の
    // lifecycle=pauseで実行を止める（復帰時に人のpause/stopと区別して自動解除する）。
    if (decision.candidate) {
      const patch = {};
      if (curWl.lifecycle === 'pause' && ['quota', 'qualification'].includes(curWl.lifecycle_source)) {
        patch.lifecycle = 'run';
        patch.lifecycle_source = null;
      }
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
      if (qualificationDoc.exists) {
        const selectionPolicy = policyCompiler.compileSelectionPolicy({
          strategy: strategyOf(profiles.executionPolicy),
          tiers: profiles.tiers,
          tierCeiling: decision.tier,
          qualifications: qualificationDoc,
          nowMs,
        });
        compiledAny = true;
        if (JSON.stringify(curWl.selection_policy || null) !== JSON.stringify(selectionPolicy)) {
          patch.selection_policy = selectionPolicy;
        }
        if (!selectionPolicy.candidates.length) {
          patch.lifecycle = 'pause';
          patch.lifecycle_source = 'qualification';
          patch.selection_reason = '適格な候補がないため保留';
        }
      }
      if (Object.keys(patch).length > 0) controlPatch[wl] = patch;
    }
    const prev = profiles.state[wl];
    if (
      !prev ||
      prev.target_tier !== decision.targetTier ||
      prev.target_since !== decision.targetSince ||
      prev.tier !== decision.tier ||
      prev.reason !== decision.reason ||
      !sameCandidate(prev.candidate, decision.candidate)
    ) {
      nextState[wl] = {
        target_tier: decision.targetTier, target_since: decision.targetSince,
        tier: decision.tier, candidate: decision.candidate, since: decision.since, reason: decision.reason,
      };
      stateDirty = true;
    }
  }

  if (Object.keys(controlPatch).length > 0 || compiledAny) {
    control.saveControl(cfg, {
      ...(compiledAny ? {
        version: 2,
        valid_until: new Date(nowMs + 15 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      } : {}),
      workloads: controlPatch,
    });
  }
  const tiersDirty = profiles.removedVariantCandidates.length > 0;
  if (stateDirty || tiersDirty) {
    const next = { ...(profiles.raw || {}) };
    next.version = 1;
    if (tiersDirty) next.tiers = profiles.tiers;
    next.state = nextState;
    next.updated_at = nowStamp();
    next.updated_by = 'dashboard';
    atomicWriteJson(path.join(dir, PROFILES_FILE), next);
  }
  return {
    decisions,
    controlWritten: Object.keys(controlPatch).length > 0 || compiledAny,
    stateWritten: stateDirty || tiersDirty,
  };
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
