'use strict';

const TIERS = ['small', 'medium', 'large'];
const POLICY_TIER = { recommended: 'medium', saving: 'small', quality: 'large' };
const POLICIES = Object.keys(POLICY_TIER);
// 「エージェントを最適化する」が効いていないとき（設定で OFF、またはローカル実行系 agent-herd が
// 無い）に選べる起動方針。節約 / 品質重視（small / large tier）は最適化があって初めて意味を持つ。
const BASIC_POLICIES = ['recommended'];
const SKILL_MODES = ['auto', 'manual', 'off'];
const MAX_INSTRUCTION_CHARS = 8000;
// 起動方針「共有」。tier を持たず、依頼を LAN の参加者へ渡す（src/main/share/）。
const SHARED_POLICY = 'shared';
// 引き受け方（accept）… 'auto' 自動で拾う / 'manual' 選んだものだけ / 'off' 受けない
const ACCEPT_MODES = ['auto', 'manual', 'off'];
const SHARE_DEFAULTS = {
  enabled: false, node: '', passphrase: '', port: 47801, udp: true, peers: [],
  accept: 'off', clis: [], acceptWrite: false, maxConcurrent: 1, dailyCap: 20, perRequesterDailyCap: 5,
};

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

function bounded(value, fallback, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, Math.floor(number)));
}

// 引き受け方。accept が無い保存値は、以前の participate（受ける／受けない）から読み替える。
function acceptMode(source) {
  if (ACCEPT_MODES.includes(source.accept)) return source.accept;
  return source.participate ? 'auto' : 'off';
}

// 設定 > 共有。port 0 は空いているポート、上限の 0 は無制限。
function share(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    enabled: Boolean(source.enabled),
    node: String(source.node || '').trim().slice(0, 60),
    passphrase: String(source.passphrase || ''),
    port: bounded(source.port, SHARE_DEFAULTS.port, 0, 65535),
    udp: source.udp !== false,
    peers: uniqueStrings(source.peers).slice(0, 50),
    accept: acceptMode(source),
    // 以前の版の「受ける／受けない」。引き受け方から導き、古い画面や保存値とも噛み合わせる
    participate: acceptMode(source) !== 'off',
    clis: uniqueStrings(source.clis).map((name) => name.toLowerCase()),
    acceptWrite: Boolean(source.acceptWrite),
    maxConcurrent: bounded(source.maxConcurrent, SHARE_DEFAULTS.maxConcurrent, 1, 4),
    dailyCap: bounded(source.dailyCap, SHARE_DEFAULTS.dailyCap, 0, 1000),
    perRequesterDailyCap: bounded(source.perRequesterDailyCap, SHARE_DEFAULTS.perRequesterDailyCap, 0, 1000),
  };
}

function normalize(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const legacy = { cli: String(source.lastCli || 'copilot'), model: String(source.lastModel || '') };
  const execution = source.execution && typeof source.execution === 'object' ? source.execution : {};
  const tiers = execution.tiers && typeof execution.tiers === 'object' ? execution.tiers : {};
  const instructions = source.instructions && typeof source.instructions === 'object' ? source.instructions : {};
  const rawSkillSelection = instructions.skillSelection && typeof instructions.skillSelection === 'object'
    ? instructions.skillSelection : {};
  const skillCandidates = uniqueStrings(Object.hasOwn(rawSkillSelection, 'candidates')
    ? rawSkillSelection.candidates : instructions.skills);
  return {
    instructions: {
      enabled: instructions.enabled !== false,
      text: String(instructions.text || '').trim().slice(0, MAX_INSTRUCTION_CHARS),
      // 別のフォルダへ書き込む必要が出たとき、AI に分岐の依頼（@fork 行）を書かせる作法を添えるか
      forkEnabled: instructions.forkEnabled !== false,
      skills: skillCandidates,
      skillSelection: {
        enabled: rawSkillSelection.enabled !== false,
        defaultMode: SKILL_MODES.includes(rawSkillSelection.defaultMode) ? rawSkillSelection.defaultMode : 'auto',
        candidates: skillCandidates,
      },
      startupActions: startupActions(instructions.startupActions),
    },
    execution: {
      defaultPolicy: POLICIES.includes(execution.defaultPolicy) ? execution.defaultPolicy : 'recommended',
      optimizeAgents: execution.optimizeAgents !== false,
      defaultAutoApprove: Boolean(execution.defaultAutoApprove),
      defaultReadonly: Object.hasOwn(execution, 'defaultReadonly')
        ? Boolean(execution.defaultReadonly) : Boolean(source.lastReadonly),
      maxConcurrent: concurrent(execution.maxConcurrent),
      tiers: Object.fromEntries(TIERS.map((tier) => [tier, pair(tiers[tier], legacy)])),
    },
    share: share(source.share),
  };
}

// 最適化が効いているか（設定の optimizeAgents と、ローカル実行系の有無の両方）。
function optimized(config, { herdAvailable = true } = {}) {
  const configured = config && config.execution ? config.execution : normalize(config).execution;
  return configured.optimizeAgents !== false && !!herdAvailable;
}

// 最適化が効いていなければ、節約 / 品質重視は「おすすめ」として扱う（別 tier へ黙って倒すのではなく、
// 選べない方針を選べる唯一の方針へ写す。画面も同じ規則で選べなくしている）。
function effectivePolicy(policy, { optimized: on = true } = {}) {
  const name = String(policy || '');
  if (name === 'direct' || name === SHARED_POLICY) return name;
  if (!POLICIES.includes(name)) return 'recommended';
  return on || BASIC_POLICIES.includes(name) ? name : 'recommended';
}

//   optimized … false なら節約 / 品質重視を「おすすめ」へ写す（呼ぶ側が herd の有無を見て決める）
function resolve(config, request = {}, { optimized: on = true } = {}) {
  const requestedPolicy = String(request.policy || '');
  if (requestedPolicy === SHARED_POLICY) {
    // 共有: CLI は「どれでも」（空）か、参加者が提供している名前。tier は持たない。
    const shareConfig = config && config.share ? config.share : normalize(config).share;
    if (!shareConfig.enabled) throw new Error('共有が設定されていません（設定 > 共有）');
    const cli = String(request.cli || '').trim().toLowerCase();
    return { policy: SHARED_POLICY, tier: '', cli: cli === '*' ? '' : cli, model: String(request.model || '').trim(), source: 'shared' };
  }
  if (requestedPolicy === 'direct' || (!POLICIES.includes(requestedPolicy) && request.cli)) {
    const cli = String(request.cli || '').trim().toLowerCase();
    if (!cli) throw new Error('直接指定するエージェントを選んでください');
    return { policy: 'direct', tier: '', cli, model: String(request.model || '').trim(), source: 'direct' };
  }
  const configured = config && config.execution ? config.execution : normalize(config).execution;
  const policy = effectivePolicy(POLICIES.includes(requestedPolicy)
    ? requestedPolicy
    : (POLICIES.includes(configured.defaultPolicy) ? configured.defaultPolicy : 'recommended'), { optimized: on });
  const tier = POLICY_TIER[policy];
  const selected = configured.tiers[tier];
  if (!selected || !String(selected.cli || '').trim()) throw new Error(`${tier} Tier のエージェントを設定してください`);
  return { policy, tier, cli: selected.cli, model: selected.model, source: 'policy' };
}

module.exports = {
  TIERS, POLICIES, BASIC_POLICIES, POLICY_TIER, SKILL_MODES, MAX_INSTRUCTION_CHARS, SHARED_POLICY, SHARE_DEFAULTS, ACCEPT_MODES,
  normalize, resolve, optimized, effectivePolicy, share, acceptMode,
};
