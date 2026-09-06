'use strict';

const VERSION = 1;
const STATUSES = new Set(['draft', 'needs-trial', 'awaiting-confirmation', 'ready']);
const SECRET_NAME = /password|passwd|token|secret|api.?key|パスワード|暗証/i;
const SECRET_VALUE = /\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)/gi;

function text(value, max = 4000) {
  return String(value || '').trim().slice(0, max);
}

function redact(value, context = '') {
  if (typeof value === 'string') return value.replace(SECRET_VALUE, '$1=***');
  if (Array.isArray(value)) return value.map((item) => redact(item, context));
  if (!value || typeof value !== 'object') return value;
  const out = {};
  const secretContext = SECRET_NAME.test(String(context || ''))
    || [value.key, value.label, value.target, value.role].some((item) => SECRET_NAME.test(String(item || '')));
  for (const [key, item] of Object.entries(value)) {
    if (SECRET_NAME.test(key) || (secretContext && ['value', 'example'].includes(key))) out[key] = '***';
    else out[key] = redact(item, key);
  }
  return out;
}

function list(value, map) {
  return Array.isArray(value) ? value.map(map).filter(Boolean) : [];
}

function normalizeUnderstanding(value = {}) {
  const input = value && typeof value === 'object' ? value : {};
  return redact({
    purpose: text(input.purpose),
    variables: list(input.variables, (item) => item && typeof item === 'object' ? {
      key: text(item.key, 120),
      label: text(item.label, 300),
      example: text(item.example, 1000),
      required: item.required !== false,
    } : null),
    expectedResults: list(input.expectedResults, (item) => text(item, 1000)).filter(Boolean),
    importantActions: list(input.importantActions, (item) => item && typeof item === 'object' ? {
      id: text(item.id, 120),
      action: text(item.action, 300),
      target: text(item.target, 500),
      effect: text(item.effect, 1000),
    } : null),
    unknowns: list(input.unknowns, (item) => text(item, 1000)).filter(Boolean),
  });
}

function normalizeSession(value = {}) {
  const input = value && typeof value === 'object' ? value : {};
  const base = createSession({ machine: input.machine, title: input.title });
  return {
    ...base,
    status: STATUSES.has(input.status) ? input.status : 'draft',
    messages: list(input.messages, (item) => {
      if (!item || !['user', 'assistant'].includes(item.role) || !text(item.text)) return null;
      return { role: item.role, text: text(redact(item.text)), ...(item.kind ? { kind: text(item.kind, 80) } : {}) };
    }),
    evidence: list(input.evidence, (item) => item && typeof item === 'object' ? redact({
      id: text(item.id, 120),
      type: text(item.type, 80),
      summary: text(item.summary, 1000),
      actions: Array.isArray(item.actions) ? item.actions : [],
      capturedAt: text(item.capturedAt, 80),
    }) : null),
    understanding: normalizeUnderstanding(input.understanding),
    generations: list(input.generations, (item) => item && typeof item === 'object' ? redact(item) : null),
    activeGenerationId: text(input.activeGenerationId, 120),
    lastSuccessfulGenerationId: text(input.lastSuccessfulGenerationId, 120),
    pendingRequest: input.pendingRequest && typeof input.pendingRequest === 'object' ? redact(input.pendingRequest) : null,
    pendingApproval: input.pendingApproval && typeof input.pendingApproval === 'object' ? redact(input.pendingApproval) : null,
    trials: list(input.trials, (item) => item && typeof item === 'object' ? redact(item) : null),
  };
}

function addGeneration(value, generation = {}) {
  const session = normalizeSession(value);
  const id = text(generation.id, 120) || `generation-${session.generations.length + 1}`;
  if (session.generations.some((item) => item.id === id)) throw new Error('同じ候補世代が既にあります');
  const normalized = redact({
    id,
    createdAt: text(generation.createdAt, 80),
    summary: text(generation.summary, 1000),
    jobSpec: generation.jobSpec && typeof generation.jobSpec === 'object' ? generation.jobSpec : {},
    makerSpec: generation.makerSpec && typeof generation.makerSpec === 'object' ? generation.makerSpec : {},
  });
  session.generations.push(normalized);
  session.activeGenerationId = id;
  session.status = 'needs-trial';
  session.pendingRequest = null;
  return session;
}

function addEvidence(value, evidence = {}) {
  const session = normalizeSession(value);
  const item = redact({
    id: text(evidence.id, 120) || `evidence-${session.evidence.length + 1}`,
    type: text(evidence.type, 80) || 'demonstration',
    summary: text(evidence.summary, 1000) || '操作の見本',
    actions: Array.isArray(evidence.actions) ? evidence.actions : [],
    capturedAt: text(evidence.capturedAt, 80),
  });
  session.evidence.push(item);
  session.messages.push({ role: 'assistant', kind: 'evidence', text: `見本を受け取りました: ${item.summary}` });
  return session;
}

function recordTrial(value, trial = {}) {
  const session = normalizeSession(value);
  const generationId = text(trial.generationId, 120) || session.activeGenerationId;
  if (!session.generations.some((item) => item.id === generationId)) throw new Error('試運転する候補が見つかりません');
  const outcome = ['passed', 'failed', 'approval-required'].includes(trial.outcome) ? trial.outcome : 'failed';
  session.trials.push(redact({
    id: text(trial.id, 120) || `trial-${session.trials.length + 1}`,
    generationId,
    outcome,
    summary: text(trial.summary, 2000),
    expected: trial.expected || [],
    observed: trial.observed || [],
  }));
  session.activeGenerationId = generationId;
  session.status = outcome === 'passed' ? 'awaiting-confirmation' : 'needs-trial';
  return session;
}

function confirmReady(value, generationId = '') {
  const session = normalizeSession(value);
  const id = text(generationId, 120) || session.activeGenerationId;
  const passed = session.trials.some((trial) => trial.generationId === id && trial.outcome === 'passed');
  if (!passed) throw new Error('成功した試運転を確認してから利用可能にしてください');
  session.activeGenerationId = id;
  session.lastSuccessfulGenerationId = id;
  session.status = 'ready';
  session.pendingRequest = null;
  return session;
}

function restoreLastSuccessful(value) {
  const session = normalizeSession(value);
  if (!session.lastSuccessfulGenerationId
    || !session.generations.some((item) => item.id === session.lastSuccessfulGenerationId)) {
    throw new Error('戻せる成功版がありません');
  }
  session.activeGenerationId = session.lastSuccessfulGenerationId;
  session.status = 'ready';
  session.pendingApproval = null;
  session.pendingRequest = null;
  return session;
}

function createSession({ machine = '', title = '', purpose = '' } = {}) {
  const normalizedPurpose = text(purpose);
  return {
    version: VERSION,
    machine: text(machine, 120),
    title: text(title, 300),
    status: 'draft',
    messages: normalizedPurpose ? [{ role: 'user', text: normalizedPurpose }] : [],
    evidence: [],
    understanding: {
      purpose: normalizedPurpose,
      variables: [],
      expectedResults: [],
      importantActions: [],
      unknowns: [],
    },
    generations: [],
    activeGenerationId: '',
    lastSuccessfulGenerationId: '',
    pendingRequest: null,
    pendingApproval: null,
    trials: [],
  };
}

module.exports = {
  VERSION,
  createSession,
  normalizeSession,
  addEvidence,
  addGeneration,
  recordTrial,
  confirmReady,
  restoreLastSuccessful,
  redact,
};
