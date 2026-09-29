'use strict';

// agent-loop の設定をアプリ側でも構造として読み、ステートマシンを起動するだけの entry を
// 独立タスクにせず、対応するタスクの既定入力として扱う。
const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

const CANDIDATES = [
  ['.agents', 'agent-loop.yaml'], ['.agents', 'agent-loop.yml'],
  ['.agent', 'agent-loop.yaml'], ['.agent', 'agent-loop.yml'],
];
const RUNTIME_VALUES = new Set([
  'last_output', 'history', 'step_count', 'today', 'now', 'check_ok', 'context',
  'current_state', 'check_status', 'check_output',
]);
const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}/g;

// workflow.yaml の `inputs:`（利用者が入れる値の宣言）。書式の正典は statemachine-use の
// references/schema.md「入力の宣言」と engine.normalize_inputs。壊れた項目は落とす
// ——実行はエンジンの検証で止まるので、画面が推測で埋めない。宣言が無ければ null。
const INPUT_TYPES = new Set(['text', 'multiline', 'number', 'date', 'month', 'email', 'url', 'choice']);
const INPUT_KEY = /^[A-Za-z_][A-Za-z0-9_-]*$/;

function declaredInputs(definition) {
  const raw = definition && definition.inputs;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const scalar = (value) => value == null || ['string', 'number', 'boolean'].includes(typeof value);
  const out = [];
  for (const [key, value] of Object.entries(raw)) {
    const spec = value == null ? {} : value;
    if (!INPUT_KEY.test(key) || RUNTIME_VALUES.has(key) || typeof spec !== 'object' || Array.isArray(spec)) continue;
    const type = INPUT_TYPES.has(String(spec.type || 'text')) ? String(spec.type || 'text') : 'text';
    const options = type === 'choice' && Array.isArray(spec.options)
      ? spec.options.filter((item) => item != null && scalar(item)).map(String) : [];
    if (type === 'choice' && !options.length) continue;
    out.push({
      key,
      label: String(spec.label || '').trim() || key,
      description: String(spec.description || '').trim(),
      required: spec.required !== false,
      default: spec.default == null || !scalar(spec.default) ? null : String(spec.default),
      type,
      options,
    });
  }
  return out;
}

function readDefinition(root, machine) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(String(machine || ''))) return null;
  let definition;
  try { definition = YAML.parse(fs.readFileSync(path.join(root, '.statemachine', machine, 'workflow.yaml'), 'utf8')); } catch { return null; }
  return definition && typeof definition === 'object' && !Array.isArray(definition) ? definition : null;
}

// 宣言した入力（無ければ null）。
function definitionInputs(root, machine) {
  return declaredInputs(readDefinition(root, machine));
}

function definitionParameters(root, machine) {
  const definition = readDefinition(root, machine);
  if (!definition) return null;
  const declared = declaredInputs(definition);
  if (declared) return declared.map((item) => item.key);
  const base = path.join(root, '.statemachine', machine);
  const context = definition.context && typeof definition.context === 'object' && !Array.isArray(definition.context)
    ? definition.context : {};
  const required = new Set();
  const defaults = new Set();
  for (const [name, value] of Object.entries(context)) {
    (value == null || !String(value).trim() ? required : defaults).add(name);
  }
  const outputs = new Set();
  const templates = new Set();
  const references = new Set();
  const visit = (value) => {
    if (Array.isArray(value)) { value.forEach(visit); return; }
    if (value && typeof value === 'object') {
      if (typeof value.output_key === 'string' && value.output_key.trim()) {
        outputs.add(value.output_key.trim().split('.')[0]);
      }
      for (const [key, child] of Object.entries(value)) {
        if (typeof child === 'string' && (key.endsWith('_file') || (key === 'action' || key === 'condition') && child.startsWith('file:'))) {
          references.add(child.replace(/^file:/, '').trim());
        }
        visit(child);
      }
      return;
    }
    if (typeof value === 'string') {
      for (const match of value.matchAll(PLACEHOLDER)) templates.add(match[1]);
    }
  };
  visit(definition.states || {});
  visit(definition.transitions || []);
  if (definition.states && typeof definition.states === 'object' && !Array.isArray(definition.states)) {
    for (const [stateId, state] of Object.entries(definition.states)) {
      if (state && typeof state === 'object' && !state.action && !state.action_file) {
        references.add(`actions/${stateId}.md`);
      }
    }
  }
  if (Array.isArray(definition.transitions)) {
    for (const transition of definition.transitions) {
      if (!transition || typeof transition !== 'object' || transition.condition || transition.condition_file || transition.condition_rule) continue;
      if (typeof transition.from === 'string' && typeof transition.to === 'string') {
        references.add(`conditions/${transition.from === '*' ? 'wildcard' : transition.from}_to_${transition.to}.md`);
      }
    }
  }
  for (const name of references) {
    if (!name) continue;
    try {
      const directory = fs.realpathSync(base);
      const file = fs.realpathSync(path.resolve(base, name));
      if (file.startsWith(`${directory}${path.sep}`)) visit(fs.readFileSync(file, 'utf8'));
    } catch { /* 存在しない参照は実行時に検出する */ }
  }
  for (const name of templates) {
    const top = name.split('.')[0];
    if (top === 'context') {
      const key = name.split('.')[1];
      if (key && !Object.hasOwn(context, key)) required.add(name);
      continue;
    }
    if (!RUNTIME_VALUES.has(top) && !outputs.has(top) && !defaults.has(top)) required.add(name);
  }
  return [...required].sort();
}

function machineName(value) {
  const raw = String(value || '').trim().replace(/\\/g, '/');
  if (!raw) return '';
  if (!raw.includes('/')) return /^[A-Za-z0-9_.-]+$/.test(raw) ? raw : '';
  const parts = raw.split('/').filter(Boolean);
  const workflow = /\.ya?ml$/i.test(parts.at(-1)) ? parts.slice(0, -1) : parts;
  return workflow.at(-1) || '';
}

function readEntries(root) {
  for (const parts of CANDIDATES) {
    const file = path.join(root, ...parts);
    let text;
    try { text = fs.readFileSync(file, 'utf8'); } catch { continue; }
    let value;
    try { value = YAML.parse(text); } catch { return []; }
    return Array.isArray(value && value.prompts) ? value.prompts.filter((item) => item && typeof item === 'object') : [];
  }
  return [];
}

function scalarInputs(value) {
  const out = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return out;
  for (const [key, item] of Object.entries(value)) {
    if (item == null || typeof item === 'object') continue;
    out[String(key)] = String(item);
  }
  return out;
}

function enrichSnapshot(root, snapshot) {
  const result = { ...(snapshot || {}) };
  const tasks = Array.isArray(result.tasks) ? result.tasks : [];
  const entries = readEntries(root);
  const paired = new Map();
  for (const entry of entries) {
    const machine = machineName(entry.statemachine);
    if (!machine) continue;
    const inputs = scalarInputs(entry.input);
    const names = [...Object.keys(inputs), ...(Array.isArray(entry.parameters) ? entry.parameters.map(String) : [])];
    const previous = paired.get(machine) || { parameters: [], defaults: {}, entries: [] };
    paired.set(machine, {
      parameters: [...new Set([...previous.parameters, ...names])],
      defaults: { ...previous.defaults, ...inputs }, entries: [...previous.entries, entry],
    });
  }
  const entryMachine = (task) => task && task.kind === 'prompt'
    ? machineName((task.entry && task.entry.statemachine) || task.statemachine) : '';
  result.tasks = tasks
    .filter((task) => !entryMachine(task))
    .map((task) => {
      const machine = String(task.machine || '').trim();
      const extra = paired.get(machine);
      const defined = task.kind === 'statemachine' ? definitionParameters(root, machine) : null;
      const declared = task.kind === 'statemachine' ? definitionInputs(root, machine) : null;
      if (!extra && defined === null) return task;
      const entrySchedules = tasks.filter((item) => entryMachine(item) === machine)
        .flatMap((item) => item.schedules || (item.schedule ? [item.schedule] : []));
      const parameters = defined === null
        ? [...new Set([...(task.parameters || []), ...extra.parameters])]
        : defined;
      const declaredDefaults = Object.fromEntries((declared || [])
        .filter((item) => item.default != null && item.default !== '').map((item) => [item.key, item.default]));
      const defaults = Object.fromEntries(Object.entries({ ...declaredDefaults, ...(task.parameterDefaults || {}), ...(extra?.defaults || {}) })
        .filter(([name]) => parameters.includes(name)));
      return {
        ...task,
        parameters,
        parameterDefaults: defaults,
        ...(declared ? { inputs: declared } : {}),
        schedules: [...(task.schedules || (task.schedule ? [task.schedule] : [])), ...entrySchedules],
        loopEntries: extra?.entries.length || 0,
      };
    });
  return result;
}

// 宣言（task.inputs）があれば必須はその required で決め、任意の空は空文字で渡す
// （素の `{{key}}` を課題文に残さない）。宣言が無ければ従来どおり全部を必須とみなす。
function requiredInput(task, supplied) {
  const values = { ...((task && task.parameterDefaults) || {}), ...((supplied && typeof supplied === 'object') ? supplied : {}) };
  const blank = (name) => !String(values[name] == null ? '' : values[name]).trim();
  if (task && Array.isArray(task.inputs)) {
    for (const item of task.inputs) if (!item.required && blank(item.key)) values[item.key] = '';
    return { values, missing: task.inputs.filter((item) => item.required && blank(item.key)).map((item) => item.key) };
  }
  const required = [...new Set((task && task.parameters || []).map(String))];
  return { values, missing: required.filter(blank) };
}

function renameReferences(root, before, after) {
  if (before === after) return 0;
  let changed = 0;
  for (const parts of CANDIDATES) {
    const file = path.join(root, ...parts);
    let source;
    try { source = fs.readFileSync(file, 'utf8'); } catch { continue; }
    const doc = YAML.parseDocument(source);
    if (doc.errors.length) continue;
    const prompts = doc.get('prompts');
    if (!YAML.isSeq(prompts)) continue;
    for (const item of prompts.items) {
      if (!YAML.isMap(item)) continue;
      const value = item.get('statemachine');
      if (machineName(value) !== before) continue;
      const raw = String(value || '');
      item.set('statemachine', raw.includes('/') ? raw.replace(before, after) : after);
      changed += 1;
    }
    if (changed) fs.writeFileSync(file, String(doc), 'utf8');
    break;
  }
  return changed;
}

module.exports = { machineName, readEntries, declaredInputs, definitionInputs, definitionParameters, enrichSnapshot, requiredInput, renameReferences };
