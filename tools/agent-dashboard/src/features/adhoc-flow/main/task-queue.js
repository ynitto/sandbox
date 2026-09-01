'use strict';

// ワークフローの「作成」と「実行」を分離する実行待ちストア。
// create は agent-flow を呼ばず、execute が成功した場合だけ待ち行列から取り除く。

const fs = require('fs');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const ID_RE = /^wft-[a-zA-Z0-9-]{1,80}$/;

function resolveDir(config) {
  return String(config && config.adhocFlow && config.adhocFlow.taskQueueDir || '').trim()
    || agentHomeSubdir('flow', 'task-queue');
}

function fileOf(config, id) {
  const value = String(id || '').trim();
  if (!ID_RE.test(value)) throw new Error(`実行待ちタスクIDが不正です: ${value || '(空)'}`);
  return path.join(resolveDir(config), `${value}.json`);
}

function writeAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, file);
}

function newId() {
  return `wft-${Date.now().toString(36)}-${Math.floor(1000 + Math.random() * 9000)}`;
}

function normalize(raw) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const title = String(value.title || '').trim();
  const request = String(value.request || '').trim();
  if (!title) throw new Error('タスク名は必須です');
  if (!request) throw new Error('実行内容は必須です');
  return {
    version: 1,
    id: value.id || newId(),
    title,
    request,
    cwd: String(value.cwd || '').trim(),
    selection: value.selection && typeof value.selection === 'object' ? value.selection : { type: 'auto' },
    executionOverrides: value.executionOverrides && typeof value.executionOverrides === 'object'
      ? value.executionOverrides : null,
    // 分け方（L2）。実行資源（L4）の executionOverrides とは別の層なので別キーで持つ。
    // 空文字は「未指定＝ノード側の設定に従う」で、submit 側が値を書かない。
    granularity: String(value.granularity || '').trim(),
    splitPolicy: String(value.splitPolicy || '').trim(),
    sourceMode: String(value.sourceMode || 'new'),
    sources: Array.isArray(value.sources) ? value.sources : [],
    warnings: Array.isArray(value.warnings) ? value.warnings.map(String) : [],
    createdAt: value.createdAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

function create(config, raw) {
  const task = normalize(raw);
  writeAtomic(fileOf(config, task.id), task);
  return task;
}

function get(config, id) {
  try { return JSON.parse(fs.readFileSync(fileOf(config, id), 'utf8')); }
  catch { return null; }
}

function list(config) {
  const dir = resolveDir(config);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((name) => name.endsWith('.json')).flatMap((name) => {
    try { return [JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'))]; }
    catch { return []; }
  }).sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
}

function remove(config, id) {
  const file = fileOf(config, id);
  if (!fs.existsSync(file)) return false;
  fs.rmSync(file);
  return true;
}

// `parameters` は保存形テンプレートの `{{key}}` に入れる値。検証（未定義キー・未入力の
// 拒否）と置換は submit 側の 1 実装が行うので、ここは運ぶだけ。
function execute(config, id, submit, parameters = null) {
  const task = get(config, id);
  if (!task) throw new Error(`実行待ちタスクが見つかりません: ${id}`);
  const result = submit({
    title: task.title,
    cwd: task.cwd,
    request: task.request,
    selection: task.selection,
    executionOverrides: task.executionOverrides,
    granularity: task.granularity,
    splitPolicy: task.splitPolicy,
    ...(parameters ? { parameters } : {}),
  });
  remove(config, id);
  return { ...result, task };
}

module.exports = { resolveDir, normalize, create, get, list, remove, execute };
