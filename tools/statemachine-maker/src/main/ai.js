'use strict';

// agent-tools へ渡す情報と、返ってきた構造化応答の境界。
// AI は候補を返すだけで、ここでもファイルは一切変更しない。

const crypto = require('crypto');
const model = require('./model');

const SCHEMA_VERSION = 1;
const FINDING_CATEGORIES = new Set(['consistency', 'efficiency', 'error-handling', 'edge-case']);
const FINDING_SEVERITIES = new Set(['error', 'warning', 'suggestion']);
const SECRET_VALUE = /\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*([^\s,;]+)/gi;

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function redact(value) {
  if (typeof value === 'string') return value.replace(SECRET_VALUE, '$1=***');
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== 'object') return value;
  const out = {};
  const secretField = [value.target, value.label, value.role].some((item) => /password|passwd|token|secret|api.?key|パスワード/i.test(String(item || '')));
  for (const [key, item] of Object.entries(value)) {
    if (key === 'preserved' || key === 'parameters' || key === 'example') continue;
    if (/^(password|passwd|token|secret|api[_-]?key)$/i.test(key) || (secretField && key === 'value')) {
      out[key] = '***';
    } else {
      out[key] = redact(item);
    }
  }
  return out;
}

function safeSpec(spec) {
  return redact(model.normalizeProcedure(spec));
}

function responseContract() {
  return `次の JSON オブジェクトだけを返してください。Markdown や前後の説明は禁止です。
{
  "schemaVersion": 1,
  "status": "questions または candidate",
  "summary": "短い要約",
  "questions": [{"id":"q1","text":"質問","reason":"必要な理由","example":"回答例"}],
  "candidate": null または完全な maker 仕様,
  "assumptions": ["仮定"],
  "findings": [{"category":"consistency|efficiency|error-handling|edge-case","severity":"error|warning|suggestion","stepId":"任意","title":"短い見出し","detail":"説明"}]
}
情報が足りなければ status を questions にして candidate は null にしてください。候補を返す場合は status を candidate にし、部分パッチではなく完全な maker 仕様を candidate に入れてください。`;
}

function historyText(history) {
  if (!Array.isArray(history) || !history.length) return 'なし';
  return history.map((item, index) => {
    const question = redact(String((item && item.question) || '').trim());
    const answer = redact(String((item && item.answer) || '').trim());
    return `${index + 1}. 質問: ${question}\n   回答: ${answer}`;
  }).join('\n');
}

function draftPrompt({ request = '', history = [], catalog = model.catalog() } = {}) {
  return `あなたはステートマシン設計者です。利用者の要望から Statemachine Maker で編集できる下書きを作成してください。

要望:
${redact(String(request || '').trim())}

これまでの確認:
${historyText(history)}

利用できる工程種別:
${JSON.stringify(catalog)}

要件:
- 実行可能な順序、失敗時の終了または回復経路、想定されるエッジケースを考慮する
- 人間が編集して保存する前提で、ファイル操作や実行はしない
- 不明点が結果を大きく変える場合だけ、具体的な質問を返す

${responseContract()}`;
}

function reviewPrompt({ spec, scope = { type: 'workflow' }, focus = '', history = [], catalog = model.catalog() } = {}) {
  const normalizedScope = normalizeScope(scope, spec);
  const scopeText = normalizedScope.type === 'step'
    ? `工程「${normalizedScope.stepId}」だけ。ほかの工程、メタデータ、順序、終わり方は変更禁止。`
    : 'ステートマシン全体。';
  return `あなたはステートマシンのレビュー担当です。以下を必ず確認し、必要なら改善した完全な候補を返してください。
- 整合性
- 効率性
- エラー処理
- エッジケース

見直す範囲: ${scopeText}
追加の観点: ${redact(String(focus || '').trim()) || 'なし'}

これまでの確認:
${historyText(history)}

利用できる工程種別:
${JSON.stringify(catalog)}

現在の仕様（保持用の内部データ、記録時の入力例、秘密らしい値は除外済み）:
${JSON.stringify(safeSpec(spec), null, 2)}

findings は説明用です。修正は candidate に反映してください。ファイル操作や実行はしないでください。

${responseContract()}`;
}

function repairPrompt({ originalPrompt = '', output = '', error = '' } = {}) {
  return `${String(originalPrompt)}

前回の応答は契約違反でした。エラー: ${String(error).slice(0, 1000)}
前回の応答:
${String(output).slice(0, 12000)}

内容を修正し、指定された JSON オブジェクトだけを返してください。`;
}

function normalizeScope(scope, spec) {
  if (!scope || scope.type !== 'step') return { type: 'workflow' };
  const stepId = String(scope.stepId || '').trim();
  const normalized = model.normalizeProcedure(spec);
  if (!stepId || !normalized.steps.some((step) => step.id === stepId)) throw new Error('見直す工程が見つかりません');
  return { type: 'step', stepId };
}

function parseJsonOnly(output) {
  const raw = String(output || '').trim();
  let body = raw;
  const fenced = raw.match(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i);
  if (fenced) body = fenced[1].trim();
  if (!body.startsWith('{') || !body.endsWith('}')) throw new Error('AIの応答がJSONだけになっていません');
  try { return JSON.parse(body); } catch { throw new Error('AIの応答JSONを読み取れません'); }
}

function shortText(value, max = 4000) {
  return String(value || '').trim().slice(0, max);
}

function normalizeQuestions(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => ({
    id: shortText(item && item.id, 80) || `q${index + 1}`,
    text: shortText(item && item.text, 1000),
    reason: shortText(item && item.reason, 1000),
    example: shortText(item && item.example, 1000),
  })).filter((item) => item.text);
}

function normalizeFindings(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => ({
    category: FINDING_CATEGORIES.has(item && item.category) ? item.category : 'consistency',
    severity: FINDING_SEVERITIES.has(item && item.severity) ? item.severity : 'suggestion',
    stepId: shortText(item && item.stepId, 120),
    title: shortText(item && item.title, 300),
    detail: shortText(item && item.detail, 3000),
  })).filter((item) => item.title || item.detail);
}

function restorePrivateFields(candidate, base) {
  if (!base) return candidate;
  candidate.preserved = clone(base.preserved || {});
  const baseSteps = new Map(base.steps.map((step) => [step.id, step]));
  for (const step of candidate.steps) {
    const original = baseSteps.get(step.id);
    if (!original || !Array.isArray(step.recorded)) continue;
    const examples = new Map();
    for (const record of original.recorded || []) {
      if (!Object.prototype.hasOwnProperty.call(record, 'example')) continue;
      const key = [record.op, record.target, record.label, record.value].map((item) => String(item || '')).join('\0');
      if (!examples.has(key)) examples.set(key, []);
      examples.get(key).push(record.example);
    }
    for (const record of step.recorded) {
      const key = [record.op, record.target, record.label, record.value].map((item) => String(item || '')).join('\0');
      const values = examples.get(key);
      if (values && values.length) record.example = values.shift();
    }
  }
  return candidate;
}

function comparableStepScope(spec, selectedId) {
  const copy = clone(spec);
  delete copy.parameters;
  delete copy.preserved;
  const order = copy.steps.map((step) => step.id);
  const selected = copy.steps.find((step) => step.id === selectedId);
  copy.steps = copy.steps.filter((step) => step.id !== selectedId);
  return { outside: { ...copy, order }, selected };
}

function validateStepScope(base, candidate, stepId) {
  const before = comparableStepScope(base, stepId);
  const after = comparableStepScope(candidate, stepId);
  if (!after.selected || JSON.stringify(before.outside) !== JSON.stringify(after.outside)) {
    throw new Error('AI候補が見直し範囲外を変更しました');
  }
}

function parseEnvelope(output, { mode = 'draft', baseSpec = null, scope = { type: 'workflow' } } = {}) {
  const raw = parseJsonOnly(output);
  if (!raw || raw.schemaVersion !== SCHEMA_VERSION) throw new Error('AI応答のschemaVersionが一致しません');
  if (!['questions', 'candidate'].includes(raw.status)) throw new Error('AI応答のstatusが不正です');
  const questions = normalizeQuestions(raw.questions);
  const common = {
    schemaVersion: SCHEMA_VERSION,
    status: raw.status,
    summary: shortText(raw.summary, 2000),
    questions,
    assumptions: Array.isArray(raw.assumptions) ? raw.assumptions.map((v) => shortText(v, 1000)).filter(Boolean) : [],
    findings: normalizeFindings(raw.findings),
  };
  if (raw.status === 'questions') {
    if (!questions.length) throw new Error('AIが質問待ちを返しましたが、質問がありません');
    if (raw.candidate != null) throw new Error('質問待ちのAI応答に候補が含まれています');
    return { ...common, candidate: null, warnings: [] };
  }
  if (!raw.candidate || typeof raw.candidate !== 'object' || Array.isArray(raw.candidate)) {
    throw new Error('AI応答に完全な候補がありません');
  }
  const base = baseSpec ? model.normalizeProcedure(baseSpec) : null;
  let candidate = model.normalizeProcedure(raw.candidate);
  candidate = restorePrivateFields(candidate, base);
  candidate = model.normalizeProcedure(candidate);
  const compiled = model.compile(candidate);
  const errors = model.validateWorkflow(compiled.workflow, compiled.files);
  if (errors.length) throw new Error(`AI候補を検証できません: ${errors.join(' / ')}`);
  if (mode === 'review') {
    if (!base) throw new Error('見直し元の仕様がありません');
    if (candidate.machine !== base.machine) throw new Error('AI候補が保存名を変更しました');
    const normalizedScope = normalizeScope(scope, base);
    if (normalizedScope.type === 'step') validateStepScope(base, candidate, normalizedScope.stepId);
  }
  return { ...common, candidate, questions: [], warnings: model.portabilityWarnings(candidate) };
}

function fingerprint(spec) {
  const normalized = model.normalizeProcedure(spec);
  return crypto.createHash('sha256').update(JSON.stringify(normalized)).digest('hex');
}

module.exports = {
  SCHEMA_VERSION,
  safeSpec,
  draftPrompt,
  reviewPrompt,
  repairPrompt,
  normalizeScope,
  parseEnvelope,
  fingerprint,
};
