'use strict';

// AI が返した完全な候補を、利用者が選べる意味単位へ分解して安全に反映する。

const model = require('./model');

const META_FIELDS = ['name', 'machine', 'purpose', 'finish', 'notes', 'maxSteps'];
const CONTENT_FIELDS = ['kind', 'title', 'detail', 'target', 'check', 'checkRetries', 'recorded'];
const FLOW_FIELDS = ['outcomes', 'rawTransitions'];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function pick(value, fields) {
  return Object.fromEntries(fields.map((field) => [field, clone(value[field])]).filter(([, item]) => item !== undefined));
}

function equal(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function change(id, type, title, before, after, stepId = '') {
  return { id, type, title, before: clone(before), after: clone(after), ...(stepId ? { stepId } : {}) };
}

function diff(baseInput, candidateInput) {
  const base = model.normalizeProcedure(baseInput);
  const candidate = model.normalizeProcedure(candidateInput);
  const changes = [];
  const beforeMeta = pick(base, META_FIELDS);
  const afterMeta = pick(candidate, META_FIELDS);
  if (!equal(beforeMeta, afterMeta)) changes.push(change('metadata', 'metadata', '基本情報を変更', beforeMeta, afterMeta));

  const beforeIds = base.steps.map((step) => step.id);
  const afterIds = candidate.steps.map((step) => step.id);
  if (!equal(beforeIds, afterIds)) {
    changes.push(change('structure', 'structure', '工程の追加・削除・並べ替え', base.steps, candidate.steps));
  } else {
    base.steps.forEach((beforeStep, index) => {
      const afterStep = candidate.steps[index];
      const beforeContent = pick(beforeStep, CONTENT_FIELDS);
      const afterContent = pick(afterStep, CONTENT_FIELDS);
      if (!equal(beforeContent, afterContent)) {
        changes.push(change(`step:${beforeStep.id}:content`, 'step-content', `「${beforeStep.title || beforeStep.id}」の内容を変更`, beforeContent, afterContent, beforeStep.id));
      }
      const beforeFlow = pick(beforeStep, FLOW_FIELDS);
      const afterFlow = pick(afterStep, FLOW_FIELDS);
      if (!equal(beforeFlow, afterFlow)) {
        changes.push(change(`step:${beforeStep.id}:flow`, 'step-flow', `「${beforeStep.title || beforeStep.id}」の分岐を変更`, beforeFlow, afterFlow, beforeStep.id));
      }
    });
  }

  const beforeEnds = { terminals: base.terminals, ends: base.ends };
  const afterEnds = { terminals: candidate.terminals, ends: candidate.ends };
  if (!equal(beforeEnds, afterEnds)) changes.push(change('terminals', 'terminals', '終わり方を変更', beforeEnds, afterEnds));
  return changes;
}

function assign(target, source, fields) {
  for (const field of fields) target[field] = clone(source[field]);
}

function apply({ base: baseInput, candidate: candidateInput, ids = [] } = {}) {
  const base = model.normalizeProcedure(baseInput);
  const candidate = model.normalizeProcedure(candidateInput);
  const available = new Map(diff(base, candidate).map((item) => [item.id, item]));
  const selected = [...new Set((ids || []).map(String))];
  if (!selected.length) throw new Error('反映する提案を選んでください');
  for (const id of selected) if (!available.has(id)) throw new Error(`提案「${id}」が現在の候補にありません`);

  const next = clone(base);
  for (const id of selected) {
    const item = available.get(id);
    if (item.type === 'metadata') assign(next, candidate, META_FIELDS);
    if (item.type === 'structure') next.steps = clone(candidate.steps);
    if (item.type === 'terminals') assign(next, candidate, ['terminals', 'ends']);
    if (item.type === 'step-content' || item.type === 'step-flow') {
      const target = next.steps.find((step) => step.id === item.stepId);
      const source = candidate.steps.find((step) => step.id === item.stepId);
      if (!target || !source) throw new Error('選択した提案の工程が見つかりません');
      assign(target, source, item.type === 'step-content' ? CONTENT_FIELDS : FLOW_FIELDS);
    }
  }
  next.preserved = clone(base.preserved || {});
  let normalized;
  try {
    normalized = model.normalizeProcedure(next);
    const compiled = model.compile(normalized);
    const errors = model.validateWorkflow(compiled.workflow, compiled.files);
    if (errors.length) throw new Error(errors.join(' / '));
  } catch (err) {
    throw new Error(`選択した提案だけでは定義が成立しません: ${err.message}`, { cause: err });
  }
  return { spec: normalized, warnings: model.portabilityWarnings(normalized), changes: selected };
}

module.exports = { diff, apply };
