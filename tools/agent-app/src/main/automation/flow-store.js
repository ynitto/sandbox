'use strict';

const fs = require('fs');
const path = require('path');
const flowModel = require('./flow-model');

function flowError(code, message, extra = {}) {
  return Object.assign(new Error(message), { code, ...extra });
}

function dirOf(root) {
  return path.join(root, '.agents', 'workflows');
}

function idOf(id) {
  const value = String(id || '').trim();
  if (!flowModel.ID_RE.test(value)) throw flowError('flow-not-found', 'ワークフローが見つかりません');
  return value;
}

function fileOf(root, id) {
  return path.join(dirOf(root), `${idOf(id)}.json`);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function list(root) {
  let entries;
  try { entries = fs.readdirSync(dirOf(root), { withFileTypes: true }); } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw flowError('flow-unreadable', 'ワークフローの一覧を読めません', { detail: err.message });
  }
  return entries.filter((entry) => entry.isFile() && entry.name.endsWith('.json')).map((entry) => {
    const id = entry.name.slice(0, -5);
    try {
      const raw = readJson(path.join(dirOf(root), entry.name));
      const result = flowModel.preview(raw);
      const workflow = result.draft;
      return {
        id,
        name: workflow.name || id,
        description: workflow.description,
        nodes: workflow.nodes.length,
        humanNodes: workflow.nodes.filter((node) => node.kind === 'human').length,
        parameterKeys: result.parameterKeys,
        updatedAt: workflow.updatedAt,
        file: `.agents/workflows/${entry.name}`,
        valid: result.ok,
      };
    } catch {
      let updatedAt = '';
      try { updatedAt = fs.statSync(path.join(dirOf(root), entry.name)).mtime.toISOString(); } catch { /* 表示できればよい */ }
      return { id, name: `(読めません) ${id}`, description: '', nodes: 0, humanNodes: 0, parameterKeys: [], updatedAt, file: `.agents/workflows/${entry.name}`, valid: false };
    }
  }).sort((a, b) => a.name.localeCompare(b.name, 'ja'));
}

function read(root, id) {
  let raw;
  try { raw = readJson(fileOf(root, id)); } catch (err) {
    if (err && err.code === 'ENOENT') throw flowError('flow-not-found', 'ワークフローが見つかりません');
    throw flowError('flow-unreadable', 'ワークフローの内容を読めません', { detail: err.message });
  }
  const result = flowModel.preview(raw);
  return { workflow: result.draft, issues: result.issues, digest: result.digest };
}

function writeAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}-${Date.now()}`;
  try {
    fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    fs.renameSync(tmp, file);
  } catch (err) {
    try { fs.unlinkSync(tmp); } catch { /* 無ければよい */ }
    throw flowError('flow-write-failed', 'ワークフローを保存できません', { detail: err.message });
  }
}

function save(root, raw, mode = 'update') {
  const result = flowModel.preview(raw);
  if (!result.ok) return { ...result, saved: false, file: '' };
  const file = fileOf(root, result.workflow.id);
  const exists = fs.existsSync(file);
  if (mode === 'create' && exists) throw flowError('flow-exists', '同じ保存名のワークフローがあります');
  if (mode !== 'create' && !exists) throw flowError('flow-not-found', '編集元のワークフローが見つかりません');
  const current = exists ? readJson(file) : null;
  const workflow = {
    ...result.workflow,
    createdAt: mode === 'create' ? new Date().toISOString() : String((current && current.createdAt) || result.workflow.createdAt),
    updatedAt: new Date().toISOString(),
  };
  writeAtomic(file, workflow);
  const saved = flowModel.preview(workflow);
  return { ...saved, saved: true, file: `.agents/workflows/${workflow.id}.json` };
}

function remove(root, id) {
  const file = fileOf(root, id);
  try { fs.unlinkSync(file); } catch (err) {
    if (err && err.code === 'ENOENT') throw flowError('flow-not-found', 'ワークフローが見つかりません');
    throw flowError('flow-write-failed', 'ワークフローを削除できません', { detail: err.message });
  }
  return { deleted: true };
}

module.exports = { flowError, dirOf, fileOf, list, read, save, remove, writeAtomic };
