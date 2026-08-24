'use strict';

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir, sharedHomeRoots, sharedStateReadPath, writeSharedStateJson } = require('../../../base/main/agent-home');

function expandHome(p) {
  return p === '~' || String(p || '').startsWith('~/') ? path.join(os.homedir(), String(p).slice(1)) : p;
}

function resolveTuningDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  return expandHome(c.tuningDir || process.env.AGENT_TUNING_DIR || agentHomeSubdir('tuning'));
}

function resolveMethodsDir(cfg) {
  const c = (cfg && cfg.orchestration) || {};
  const explicit = c.methodsDir || process.env.AGENT_METHODS_DIR;
  if (explicit) return expandHome(explicit);
  for (const root of sharedHomeRoots()) {
    const home = path.join(root, '.agents', 'methods');
    if (fs.existsSync(home)) return home;
  }
  const packaged = process.resourcesPath && path.join(process.resourcesPath, 'methods');
  if (packaged && fs.existsSync(packaged)) return packaged;
  return path.resolve(__dirname, '../../../../../../methods');
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

function defaults() {
  return { version: 1, revision: 0, enabled: true, methods: [], trials: [],
    profiles: { default: { injections: [], env: [] }, 'external-facing': { injections: [], env: [] } } };
}

function load(cfg) {
  const raw = readJson(sharedStateReadPath(resolveTuningDir(cfg), 'tuning.json'));
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : defaults();
}

function catalog(cfg) {
  const dir = resolveMethodsDir(cfg);
  let names;
  try { names = fs.readdirSync(dir).filter((name) => name.endsWith('.json')).sort(); } catch { return []; }
  return names.map((name) => readJson(path.join(dir, name)))
    .filter((method) => method && typeof method === 'object' && method.id);
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function sourceHash(method) {
  return crypto.createHash('sha256').update(JSON.stringify(canonical(method))).digest('hex').slice(0, 12);
}

function revisionOf(data) {
  const n = Number(data && data.revision);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

// **書く直前に revision を照合する**（compare-and-swap）。この契約の書き手は人・dashboard・
// `agent-audit tune --apply`・`agent-loop methods` の 4 つで、全員が読んで直して全体を
// 置き換える。照合しないと後勝ちで前の変更が消え、revision は両者とも +1 するので
// 消えたことにも気づけない。
function write(cfg, data, updatedBy, baseRevision) {
  const dir = resolveTuningDir(cfg);
  const current = revisionOf(load(cfg));
  if (current !== baseRevision) {
    throw new Error(`tuning.json が他の書き手に更新されています（revision ${baseRevision} → ${current}）。`
      + '読み直してからやり直してください');
  }
  data.version = 1;
  data.revision = baseRevision + 1;
  data.updated_at = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  data.updated_by = updatedBy;
  if (!data.profiles) data.profiles = defaults().profiles;
  writeSharedStateJson(dir, 'tuning.json', data);
  return data;
}

// 自動適用（enabled）の対象は `selection: auto` の作業ルールだけ。per-task は工程ごとに
// 人・planner が選び、engine はエンジンが run パラメータから決定的に選ぶ。画面がトグルを
// 出し分けているだけでは守れない（IPC は id を受け取るだけ）ので、書く層でも断る。
// agentcore.methods.select 側も同じ規則で自動注入から外す（二重の強制）。
function autoSelectable(method) {
  return String((method && method.selection) || 'auto') === 'auto';
}

function setMethod(cfg, payload) {
  const id = String((payload && payload.id) || '').trim();
  if (!id) throw new Error('手法 id が必要です');
  const data = load(cfg);
  const base = revisionOf(data);
  const methods = Array.isArray(data.methods) ? data.methods : [];
  if (payload.enabled !== false) {
    const item = catalog(cfg).find((method) => String(method.id) === id);
    if (item && !autoSelectable(item)) {
      throw new Error(`この作業ルールは自動適用の対象ではありません: ${id}`
        + `（selection: ${item.selection}）`);
    }
    if (item) {
      const snapshot = JSON.parse(JSON.stringify(item));
      snapshot.enabled = true;
      snapshot.source = `methods/${id}@${sourceHash(item)}`;
      data.methods = methods.filter((method) => String(method && method.id) !== id).concat(snapshot);
    } else {
      const custom = methods.find((method) => method && String(method.id) === id
        && String(method.source || '').startsWith('custom/'));
      if (!custom) throw new Error(`カタログに無い手法です: ${id}`);
      data.methods = methods.map((method) => String(method && method.id) === id
        ? { ...method, enabled: true } : method);
    }
  } else {
    let found = false;
    data.methods = methods.map((method) => {
      if (!method || String(method.id) !== id) return method;
      found = true;
      return { ...method, enabled: false };
    });
    if (!found) throw new Error(`有効化履歴の無い手法です: ${id}`);
  }
  return write(cfg, data, `agent-dashboard methods ${payload.enabled === false ? 'disable' : 'enable'}`, base);
}

function addMethod(cfg, payload) {
  const id = String((payload && payload.id) || '').trim();
  const description = String((payload && payload.description) || '').trim();
  const role = String((payload && payload.role) || 'session');
  const text = String((payload && payload.text) || '').trim();
  if (!/^[a-z0-9][a-z0-9-]*$/.test(id)) throw new Error('id は英小文字・数字・ハイフンで指定してください');
  if (!description) throw new Error('description は空にできません');
  if (!['planner', 'worker', 'verify', 'evaluator', 'session'].includes(role)) throw new Error(`未対応の role です: ${role}`);
  if (!text) throw new Error('text は空にできません');
  const when = payload.when === undefined ? {} : payload.when;
  if (!when || typeof when !== 'object' || Array.isArray(when)) throw new Error('when は JSON object で指定してください');
  const data = load(cfg);
  if (catalog(cfg).some((item) => String(item.id) === id)
      || (Array.isArray(data.methods) ? data.methods : []).some((item) => String(item && item.id) === id)) {
    throw new Error(`同じ id の手法が既にあります: ${id}`);
  }
  const method = { id, description, enabled: true,
    fragments: [{ role, text }], when, origin: 'user', source: `custom/${id}` };
  const base = revisionOf(data);
  data.methods = (Array.isArray(data.methods) ? data.methods : []).concat(method);
  return write(cfg, data, 'agent-dashboard methods add', base);
}

function importMethod(cfg, source, newId) {
  const id = String(newId || '').trim();
  if (!/^[a-z0-9][a-z0-9-]*$/.test(id)) throw new Error('コピー先IDは英小文字・数字・ハイフンで指定してください');
  if (!source || typeof source !== 'object') throw new Error('コピー元の作業ルールが不正です');
  const data = load(cfg);
  if (catalog(cfg).some((item) => String(item.id) === id)
      || (Array.isArray(data.methods) ? data.methods : []).some((item) => String(item && item.id) === id)) {
    throw new Error(`同じIDの作業ルールが既にあります: ${id}`);
  }
  const base = revisionOf(data);
  const method = JSON.parse(JSON.stringify(source));
  delete method._from;
  delete method.catalog_source;
  method.id = id;
  method.enabled = true;
  method.origin = 'user';
  method.source = `custom/${id}`;
  data.methods = (Array.isArray(data.methods) ? data.methods : []).concat(method);
  return write(cfg, data, 'agent-dashboard methods copy', base);
}

module.exports = { resolveTuningDir, resolveMethodsDir, load, catalog, sourceHash, setMethod, addMethod, importMethod, revisionOf, autoSelectable };
