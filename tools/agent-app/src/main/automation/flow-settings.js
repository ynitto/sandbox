'use strict';

// agent-flow の設定（agent-flow.yaml）を画面から調整する。編集するのは agent-flow が
// 実際に読む 1 枚で、探し方も agent-flow と同じ（リポジトリ直下 → .agents/ → .agent/ →
// ~/.agents/。最初に見つかった 1 枚だけを使い、マージしない）。どれも無ければ
// ~/.agents/agent-flow.yaml を作る。画面が触るのは FIELDS のキーだけで、コメントと
// ほかのキーはそのまま残す。

const fs = require('fs');
const os = require('os');
const path = require('path');
const YAML = require('yaml');

const NAMES = ['agent-flow.yaml', 'agent-flow.yml', 'agent-flow.json'];

// 既定値は agent-flow の CONFIG_DEFAULTS と揃える（画面はファイルに無いキーを既定で表示する）。
const FIELDS = {
  size: { type: 'enum', values: ['small', 'medium', 'large', 'unrestricted'], default: 'small' },
  granularity: { type: 'enum', values: ['auto', 'coarse', 'fine', 'finest'], default: 'auto' },
  split_policy: { type: 'enum', values: ['behavior', 'file'], default: 'behavior' },
  plan_gate: { type: 'bool', default: false },
  review: { type: 'review', values: ['auto', true, false], default: 'auto' },
  workers: { type: 'int', min: 1, max: 16, default: 2 },
  max_iterations: { type: 'int', min: 1, max: 20, default: 3 },
  max_retries: { type: 'int', min: 1, max: 20, default: 3 },
};

function settingsError(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}

// ~/.agents を置く家。テストや埋め込み先から差し替えられるよう、bus と同じく環境変数で変えられる。
function defaultHome() {
  return process.env.AGENT_APP_FLOW_HOME || os.homedir();
}

function homeFile(home = defaultHome()) {
  return path.join(home, '.agents', 'agent-flow.yaml');
}

// agent-flow の _find_config と同じ順で探す。
function locate(root, home = defaultHome()) {
  const bases = [];
  if (root) {
    bases.push(root, path.join(root, '.agents'));
    if (path.resolve(root) !== path.resolve(home)) bases.push(path.join(root, '.agent'));
  }
  bases.push(path.join(home, '.agents'));
  for (const base of bases) {
    for (const name of NAMES) {
      const file = path.join(base, name);
      try { if (fs.statSync(file).isFile()) return { file, exists: true, home: base === path.join(home, '.agents') }; } catch { /* 次の候補 */ }
    }
  }
  return { file: homeFile(home), exists: false, home: true };
}

function isJson(file) {
  return path.extname(file).toLowerCase() === '.json';
}

function coerce(key, raw) {
  const field = FIELDS[key];
  if (raw === undefined || raw === null || raw === '') return field.default;
  if (field.type === 'enum') {
    const value = String(raw).trim().toLowerCase();
    return field.values.includes(value) ? value : field.default;
  }
  if (field.type === 'bool') {
    if (typeof raw === 'boolean') return raw;
    return ['true', 'yes', 'on', '1'].includes(String(raw).trim().toLowerCase());
  }
  if (field.type === 'review') {
    if (typeof raw === 'boolean') return raw;
    const value = String(raw).trim().toLowerCase();
    if (['true', 'yes', 'on'].includes(value)) return true;
    if (['false', 'no', 'off'].includes(value)) return false;
    return 'auto';
  }
  const n = Number(raw);
  if (!Number.isInteger(n)) return field.default;
  return Math.max(field.min, Math.min(field.max, n));
}

function parse(file, text) {
  if (isJson(file)) {
    const data = JSON.parse(text || '{}');
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('オブジェクトではありません');
    return data;
  }
  const doc = YAML.parseDocument(text || '');
  if (doc.errors.length) throw doc.errors[0];
  const data = doc.toJS() || {};
  if (typeof data !== 'object' || Array.isArray(data)) throw new Error('オブジェクトではありません');
  return data;
}

function valuesOf(data) {
  return Object.fromEntries(Object.keys(FIELDS).map((key) => [key, coerce(key, data[key])]));
}

function read(root, { home } = {}) {
  const found = locate(root, home);
  let data = {};
  if (found.exists) {
    try { data = parse(found.file, fs.readFileSync(found.file, 'utf8')); } catch (err) {
      throw settingsError('settings-unreadable', `${found.file} を読み取れません: ${err.message}`);
    }
  }
  return { file: found.file, exists: found.exists, home: found.home, values: valuesOf(data), defaults: valuesOf({}) };
}

function writeAtomic(file, body) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, body);
  fs.renameSync(tmp, file);
}

// 値が既定と同じで、ファイルにもそのキーが無いなら書き足さない（ファイルを既定値で埋めない）。
function save(root, raw, { home } = {}) {
  const found = locate(root, home);
  const text = found.exists ? fs.readFileSync(found.file, 'utf8') : '';
  const input = raw && typeof raw === 'object' ? raw : {};
  const changes = {};
  for (const key of Object.keys(FIELDS)) {
    if (Object.prototype.hasOwnProperty.call(input, key)) changes[key] = coerce(key, input[key]);
  }
  let current;
  try { current = found.exists ? parse(found.file, text) : {}; } catch (err) {
    throw settingsError('settings-unreadable', `${found.file} を読み取れません: ${err.message}`);
  }
  const keep = (key, value) => Object.prototype.hasOwnProperty.call(current, key) || value !== FIELDS[key].default;
  if (isJson(found.file)) {
    const next = { ...current };
    for (const [key, value] of Object.entries(changes)) if (keep(key, value)) next[key] = value;
    writeAtomic(found.file, `${JSON.stringify(next, null, 2)}\n`);
  } else {
    const doc = YAML.parseDocument(text);
    if (doc.contents === null || doc.contents === undefined) doc.contents = doc.createNode({});
    for (const [key, value] of Object.entries(changes)) if (keep(key, value)) doc.set(key, value);
    writeAtomic(found.file, String(doc));
  }
  return read(root, { home });
}

module.exports = { FIELDS, locate, read, save, homeFile };
