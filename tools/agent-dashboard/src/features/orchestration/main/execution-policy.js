'use strict';

const budget = require('./budget');
const profiles = require('./profiles');

const WORKLOADS = budget.KNOWN_WORKLOADS;
// workload 全体へ適用する方針は、複雑さが分かっている工程専用の basic を選ばない。
const POLICY_TIERS = ['small', 'medium', 'large'];
const SWITCH_RATIOS = { early: 0.3, standard: 0.2, late: 0.1 };
const PRIORITY_WEIGHTS = { low: 0.5, standard: 1, high: 2 };
const ON_EXHAUSTED = ['degrade', 'pause', 'stop'];

function equalWorkloads(onExhausted = 'degrade') {
  return Object.fromEntries(WORKLOADS.map((workload) => [workload, {
    weight: 1,
    min_tokens: 0,
    max_tokens: 0,
    on_exhausted: onExhausted,
  }]));
}

function nonNegative(value, label) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number < 0) throw new Error(`${label}は0以上で指定してください`);
  return number;
}

function customPreset(input) {
  const normalTier = input.normalTier || 'medium';
  if (!POLICY_TIERS.includes(normalTier)) {
    throw new Error('通常時の実行レベルは軽量・標準・高性能から選んでください。単純作業は工程ごとに指定します');
  }
  const tokenLimit = nonNegative(input.tokenLimit, '全体利用上限');
  const switchTiming = Object.hasOwn(SWITCH_RATIOS, input.switchTiming) ? input.switchTiming : 'standard';
  const threshold = SWITCH_RATIOS[switchTiming];
  const onExhausted = ON_EXHAUSTED.includes(input.onExhausted) ? input.onExhausted : 'degrade';
  let steps;
  if (normalTier === 'small') steps = [{ min_remaining_ratio: 0, tier: 'small' }];
  else if (normalTier === 'medium') {
    steps = [{ min_remaining_ratio: threshold, tier: 'medium' }, { min_remaining_ratio: 0, tier: 'small' }];
  } else {
    steps = [
      { min_remaining_ratio: threshold, tier: 'large' },
      { min_remaining_ratio: 0.05, tier: 'medium' },
      { min_remaining_ratio: 0, tier: 'small' },
    ];
  }
  const workloads = equalWorkloads(onExhausted);
  for (const [workload, override] of Object.entries(input.overrides || {})) {
    if (!WORKLOADS.includes(workload) || !override || typeof override !== 'object') continue;
    workloads[workload] = {
      ...workloads[workload],
      weight: PRIORITY_WEIGHTS[override.priority] || 1,
      max_tokens: nonNegative(override.maxTokens, `${workload}の上限`),
      on_exhausted: ON_EXHAUSTED.includes(override.onExhausted)
        ? override.onExhausted : onExhausted,
    };
  }
  return {
    noCapTier: normalTier,
    steps,
    softRatio: normalTier === 'small' ? 0 : 1 - threshold,
    tokenLimit,
    workloads,
    custom: { normalTier, tokenLimit,
      switchTiming, onExhausted, overrides: input.overrides || {} },
  };
}

function build(mode, input = {}) {
  const presets = {
    auto: {
      noCapTier: 'medium',
      steps: [
        { min_remaining_ratio: 0.2, tier: 'medium' },
        { min_remaining_ratio: 0, tier: 'small' },
      ],
      softRatio: 0.8,
    },
    saving: {
      noCapTier: 'small',
      steps: [{ min_remaining_ratio: 0, tier: 'small' }],
      softRatio: 0,
      workloads: equalWorkloads('pause'),
    },
    quality: {
      noCapTier: 'large',
      steps: [
        { min_remaining_ratio: 0.2, tier: 'large' },
        { min_remaining_ratio: 0.05, tier: 'medium' },
        { min_remaining_ratio: 0, tier: 'small' },
      ],
      softRatio: 0.95,
    },
  };
  const preset = mode === 'custom' ? customPreset(input) : presets[mode];
  if (!preset) throw new Error(`実行方針が不正です: ${mode}`);
  return {
    mode,
    budget: {
      tokens: preset.tokenLimit,
      allocation: {
        mode: 'auto',
        soft_ratio: preset.softRatio,
        rebalance_interval_sec: 300,
        workloads: preset.workloads || equalWorkloads(),
      },
    },
    profiles: {
      enabled: true,
      policy: {
        apply_to: WORKLOADS,
        steps: preset.steps,
        no_cap_tier: preset.noCapTier,
        hysteresis: 0.05,
        min_hold_sec: 900,
        interval_sec: 300,
      },
    },
    custom: preset.custom,
  };
}

function save(cfg, payload = {}) {
  const result = { ok: false, budgetSaved: false, profilesSaved: false, applied: false, errors: [] };
  let built;
  try {
    built = build(payload.mode, payload.custom || {});
  } catch (error) {
    result.errors.push({ target: 'policy', message: error.message || String(error) });
    return result;
  }
  const tiers = profiles.load(cfg).tiers || {};
  const requiredTiers = [...new Set([
    built.profiles.policy.no_cap_tier,
    ...built.profiles.policy.steps.map((step) => step.tier),
  ].filter(Boolean))];
  const missingTiers = requiredTiers.filter((tier) => !tiers[tier] || !tiers[tier].candidates.length);
  if (missingTiers.length) {
    result.errors.push({
      target: 'profiles',
      message: `候補が未設定の実行レベルがあります: ${missingTiers.join(', ')}`,
    });
    return result;
  }
  try {
    budget.save(cfg, built.budget);
    result.budgetSaved = true;
  } catch (error) {
    result.errors.push({ target: 'budget', message: error.message || String(error) });
  }
  try {
    profiles.save(cfg, {
      ...built.profiles,
      executionPolicy: { mode: built.mode, custom: built.custom || {} },
    });
    result.profilesSaved = true;
  } catch (error) {
    result.errors.push({ target: 'profiles', message: error.message || String(error) });
  }
  if (result.budgetSaved && result.profilesSaved) {
    try {
      result.apply = profiles.apply(cfg, { force: true });
      result.applied = true;
    } catch (error) {
      result.errors.push({ target: 'control', message: error.message || String(error) });
    }
  }
  result.ok = result.budgetSaved && result.profilesSaved && result.applied;
  return result;
}

module.exports = { WORKLOADS, build, save };
