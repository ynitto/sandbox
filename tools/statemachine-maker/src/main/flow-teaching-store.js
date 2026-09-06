'use strict';

const fs = require('node:fs');
const path = require('node:path');
const flowModel = require('./flow-model');
const teaching = require('./flow-teaching-model');

function workflowId(value) {
  const id = String(value || '').trim();
  if (!flowModel.ID_RE.test(id)) throw new Error('ワークフローの保存名が不正です');
  return id;
}

function dirFor(root) { return path.join(root, '.agents', 'workflows', '.teaching'); }
function fileFor(root, id) { return path.join(dirFor(root), `${workflowId(id)}.json`); }

function load(root, id) {
  const file = fileFor(root, id);
  try { return teaching.normalizeSession(JSON.parse(fs.readFileSync(file, 'utf8'))); } catch (err) {
    if (err && err.code === 'ENOENT') return teaching.createSession({ workflowId: id });
    throw new Error(`教えた内容を読み取れません: ${err.message}`, { cause: err });
  }
}

function save(root, id, value) {
  const session = teaching.normalizeSession({ ...value, workflowId: workflowId(id) });
  const file = fileFor(root, id);
  const temporary = `${file}.tmp-${process.pid}`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(session, null, 2)}\n`, { encoding: 'utf8', flag: 'w' });
  fs.renameSync(temporary, file);
  return session;
}

function create(root, value = {}) {
  const id = workflowId(value.workflowId);
  if (fs.existsSync(fileFor(root, id))) throw new Error('同じワークフローの下書きが既にあります');
  return save(root, id, teaching.createSession({ ...value, workflowId: id }));
}

function list(root) {
  let names;
  try { names = fs.readdirSync(dirFor(root)); } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw err;
  }
  return names.filter((name) => name.endsWith('.json')).sort().flatMap((name) => {
    const id = name.slice(0, -5);
    try {
      const session = load(root, id);
      const lastTrial = session.trials[session.trials.length - 1] || null;
      return [{ workflowId: id, title: session.title || id, purpose: session.understanding.purpose, status: session.status, lastTrial }];
    } catch { return []; }
  });
}

module.exports = { dirFor, fileFor, load, save, create, list };
