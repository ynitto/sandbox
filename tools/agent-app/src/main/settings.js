'use strict';

const TIERS = ['small', 'medium', 'large'];
const POLICY_TIER = { recommended: 'medium', saving: 'small', quality: 'large' };
const POLICIES = Object.keys(POLICY_TIER);
const MAX_INSTRUCTION_CHARS = 8000;

function pair(value, fallback) {
  const source = value && typeof value === 'object' ? value : {};
  return {
    cli: String(source.cli || fallback.cli),
    model: String(source.model != null ? source.model : fallback.model),
  };
}

function uniqueStrings(value) {
  return [...new Set((Array.isArray(value) ? value : [])
    .map((item) => String(item || '').trim()).filter(Boolean))];
}

function startupActions(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    if (!item || !['skill', 'command'].includes(item.type)) return null;
    const actionValue = String(item.value || '').trim();
    if (!actionValue) return null;
    return {
      type: item.type,
      value: actionValue,
      onError: item.onError === 'fail' ? 'fail' : 'warn',
    };
  }).filter(Boolean);
}

function concurrent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 2;
  return Math.max(1, Math.min(8, Math.floor(number)));
}

function normalize(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const legacy = { cli: String(source.lastCli || 'copilot'), model: String(source.lastModel || '') };
  const execution = source.execution && typeof source.execution === 'object' ? source.execution : {};
  const tiers = execution.tiers && typeof execution.tiers === 'object' ? execution.tiers : {};
  const instructions = source.instructions && typeof source.instructions === 'object' ? source.instructions : {};
  return {
    instructions: {
      enabled: instructions.enabled !== false,
      text: String(instructions.text || '').trim().slice(0, MAX_INSTRUCTION_CHARS),
      skills: uniqueStrings(instructions.skills),
      startupActions: startupActions(instructions.startupActions),
    },
    execution: {
      defaultPolicy: POLICIES.includes(execution.defaultPolicy) ? execution.defaultPolicy : 'recommended',
      defaultReadonly: Object.hasOwn(execution, 'defaultReadonly')
        ? Boolean(execution.defaultReadonly) : Boolean(source.lastReadonly),
      maxConcurrent: concurrent(execution.maxConcurrent),
      tiers: Object.fromEntries(TIERS.map((tier) => [tier, pair(tiers[tier], legacy)])),
    },
  };
}

function resolve(config, request = {}) {
  const requestedPolicy = String(request.policy || '');
  if (requestedPolicy === 'direct' || (!POLICIES.includes(requestedPolicy) && request.cli)) {
    const cli = String(request.cli || '').trim().toLowerCase();
    if (!cli) throw new Error('直接指定するエージェントを選んでください');
    return { policy: 'direct', tier: '', cli, model: String(request.model || '').trim(), source: 'direct' };
  }
  const configured = config && config.execution ? config.execution : normalize(config).execution;
  const policy = POLICIES.includes(requestedPolicy)
    ? requestedPolicy
    : (POLICIES.includes(configured.defaultPolicy) ? configured.defaultPolicy : 'recommended');
  const tier = POLICY_TIER[policy];
  const selected = configured.tiers[tier];
  if (!selected || !String(selected.cli || '').trim()) throw new Error(`${tier} Tier のエージェントを設定してください`);
  return { policy, tier, cli: selected.cli, model: selected.model, source: 'policy' };
}

module.exports = {
  TIERS, POLICIES, POLICY_TIER, MAX_INSTRUCTION_CHARS,
  normalize, resolve,
};
