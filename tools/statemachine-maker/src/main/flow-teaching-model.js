'use strict';

const { redact } = require('./teaching-model');

const VERSION = 1;
const STATUSES = new Set(['draft', 'needs-trial', 'awaiting-confirmation', 'ready']);

function text(value, max = 4000) { return String(value || '').trim().slice(0, max); }
function list(value, map = (item) => item) { return Array.isArray(value) ? value.map(map).filter(Boolean) : []; }

function normalizeUnderstanding(value = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const strings = (key) => list(source[key], (item) => text(item, 1200)).filter(Boolean);
  return redact({
    purpose: text(source.purpose),
    scope: strings('scope'),
    inputs: list(source.inputs, (item) => item && typeof item === 'object' ? redact(item) : null),
    outputContract: strings('outputContract'),
    constraints: strings('constraints'),
    nonGoals: strings('nonGoals'),
    decompositionPolicy: strings('decompositionPolicy'),
    replanningPolicy: strings('replanningPolicy'),
    humanCheckpoints: strings('humanCheckpoints'),
    qualityCriteria: strings('qualityCriteria'),
    unknowns: strings('unknowns'),
  });
}

function createSession({ workflowId = '', title = '', purpose = '' } = {}) {
  const normalizedPurpose = text(purpose);
  return {
    version: VERSION, workflowId: text(workflowId, 120), title: text(title, 300), status: 'draft',
    messages: normalizedPurpose ? [{ role: 'user', text: normalizedPurpose }] : [],
    evidence: { requestExamples: [], resultExamples: [], references: [] },
    understanding: normalizeUnderstanding({ purpose: normalizedPurpose }),
    generations: [], activeGenerationId: '', lastSuccessfulGenerationId: '', trials: [],
  };
}

function normalizeSession(value = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const base = createSession(source);
  const evidence = source.evidence && typeof source.evidence === 'object' ? source.evidence : {};
  return {
    ...base,
    status: STATUSES.has(source.status) ? source.status : 'draft',
    messages: list(source.messages, (item) => item && ['user', 'assistant'].includes(item.role) && text(item.text)
      ? { role: item.role, text: text(redact(item.text)), ...(item.kind ? { kind: text(item.kind, 80) } : {}) } : null),
    evidence: redact({
      requestExamples: list(evidence.requestExamples, (item) => text(item, 4000)).filter(Boolean),
      resultExamples: list(evidence.resultExamples, (item) => text(item, 4000)).filter(Boolean),
      references: list(evidence.references, (item) => item && typeof item === 'object' ? item : null),
    }),
    understanding: normalizeUnderstanding(source.understanding),
    generations: list(source.generations, (item) => item && typeof item === 'object' ? redact(item) : null),
    activeGenerationId: text(source.activeGenerationId, 120),
    lastSuccessfulGenerationId: text(source.lastSuccessfulGenerationId, 120),
    trials: list(source.trials, (item) => item && typeof item === 'object' ? redact(item) : null),
  };
}

function addGeneration(value, generation = {}) {
  const session = normalizeSession(value);
  const id = text(generation.id, 120) || `generation-${session.generations.length + 1}`;
  if (session.generations.some((item) => item.id === id)) throw new Error('同じ候補世代が既にあります');
  session.generations.push(redact({
    id, createdAt: text(generation.createdAt, 80) || new Date().toISOString(),
    summary: text(generation.summary, 1200), workflowSpec: generation.workflowSpec || {},
    workflow: generation.workflow || {}, digest: text(generation.digest, 120),
  }));
  session.activeGenerationId = id;
  session.status = 'needs-trial';
  return session;
}

function recordTrial(value, trial = {}) {
  const session = normalizeSession(value);
  const generationId = text(trial.generationId, 120) || session.activeGenerationId;
  if (!session.generations.some((item) => item.id === generationId)) throw new Error('試運転する候補が見つかりません');
  const outcome = ['passed', 'failed', 'approval-required'].includes(trial.outcome) ? trial.outcome : 'failed';
  session.trials.push(redact({
    id: text(trial.id, 120) || `trial-${session.trials.length + 1}`, generationId,
    runId: text(trial.runId, 160), outcome, assessment: trial.assessment || {},
  }));
  session.activeGenerationId = generationId;
  session.status = outcome === 'passed' ? 'awaiting-confirmation' : 'needs-trial';
  return session;
}

function confirmReady(value, generationId = '', digest = '') {
  const session = normalizeSession(value);
  const id = text(generationId, 120) || session.activeGenerationId;
  const generation = session.generations.find((item) => item.id === id);
  if (!generation) throw new Error('利用可能にする候補が見つかりません');
  if (text(digest, 120) && generation.digest !== text(digest, 120)) throw new Error('試運転後に候補が変更されています');
  if (!session.trials.some((trial) => trial.generationId === id && trial.outcome === 'passed')) {
    throw new Error('成功した試運転を確認してから利用可能にしてください');
  }
  session.activeGenerationId = id;
  session.lastSuccessfulGenerationId = id;
  session.status = 'ready';
  return session;
}

module.exports = { VERSION, createSession, normalizeSession, normalizeUnderstanding, addGeneration, recordTrial, confirmReady };
