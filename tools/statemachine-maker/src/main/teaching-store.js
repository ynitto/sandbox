'use strict';

const fs = require('node:fs');
const path = require('node:path');
const store = require('./store');
const teaching = require('./teaching-model');

const FILE = 'teaching.json';

function fileFor(root, machine) {
  return path.join(store.machineDir(root, machine), FILE);
}

function load(root, machine) {
  const file = fileFor(root, machine);
  let body;
  try { body = fs.readFileSync(file, 'utf8'); } catch (err) {
    if (err && err.code === 'ENOENT') return teaching.createSession({ machine });
    throw err;
  }
  try { return teaching.normalizeSession(JSON.parse(body)); } catch (err) {
    throw new Error(`教えた内容を読み取れません: ${err.message}`, { cause: err });
  }
}

function save(root, machine, value) {
  const session = teaching.normalizeSession({ ...value, machine });
  const file = fileFor(root, machine);
  const temporary = `${file}.tmp`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(session, null, 2)}\n`, 'utf8');
  fs.renameSync(temporary, file);
  return session;
}

function create(root, value = {}) {
  const machine = String(value.machine || '').trim();
  if (fs.existsSync(fileFor(root, machine))) throw new Error('同じタスクの下書きが既にあります');
  return save(root, machine, teaching.createSession({ ...value, machine }));
}

function list(root) {
  const base = path.join(root, store.DIR);
  let names;
  try { names = fs.readdirSync(base); } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw err;
  }
  const items = [];
  for (const machine of names.sort()) {
    if (!fs.existsSync(path.join(base, machine, FILE))) continue;
    try {
      const session = load(root, machine);
      items.push({
        machine,
        title: session.title || machine,
        purpose: session.understanding.purpose,
        status: session.status,
        lastTrial: session.trials.length ? session.trials[session.trials.length - 1] : null,
      });
    } catch { /* 壊れた下書きは個別に開いたとき理由を表示する */ }
  }
  return items;
}

module.exports = { FILE, fileFor, load, save, create, list };
