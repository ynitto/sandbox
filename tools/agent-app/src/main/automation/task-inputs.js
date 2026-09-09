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
      if (!extra) return task;
      const entrySchedules = tasks.filter((item) => entryMachine(item) === machine)
        .flatMap((item) => item.schedules || (item.schedule ? [item.schedule] : []));
      return {
        ...task,
        parameters: [...new Set([...(task.parameters || []), ...extra.parameters])],
        parameterDefaults: extra.defaults,
        schedules: [...(task.schedules || (task.schedule ? [task.schedule] : [])), ...entrySchedules],
        loopEntries: extra.entries.length,
      };
    });
  return result;
}

function requiredInput(task, supplied) {
  const values = { ...((task && task.parameterDefaults) || {}), ...((supplied && typeof supplied === 'object') ? supplied : {}) };
  const required = [...new Set((task && task.parameters || []).map(String))];
  return { values, missing: required.filter((name) => !String(values[name] == null ? '' : values[name]).trim()) };
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

module.exports = { machineName, readEntries, enrichSnapshot, requiredInput, renameReferences };
