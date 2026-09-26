'use strict';

// pending.md の依頼を既存のワークフロー形式へ変換する。過去の取り込み分にも対応。
const fs = require('fs');
const path = require('path');
const { createHash } = require('crypto');
const projects = require('./projects');
const flowStore = require('./automation/flow-store');
const flowModel = require('./automation/flow-model');
const importer = require('./projectImport');
const bundle = require('../shared/projectImportBundle');
const digest = text => createHash('sha256').update(text).digest('hex').slice(0, 24);

function parse(text) {
  const sections = [];
  let lines = [], fence = '';
  const flush = () => {
    const body = lines.join('\n').trim(); lines = [];
    const heading = /^## (.+)$/m.exec(body);
    if (!heading) return;
    const source = /^出典: (.+)$/m.exec(body)?.[1] || heading[1];
    sections.push({ title: heading[1], body: body.slice(heading.index), source });
  };
  for (const line of String(text || '').split(/\r?\n/)) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = '';
    }
    if (!fence && line === '---' && lines.some(value => value.startsWith('出典: '))) { flush(); continue; }
    lines.push(line);
  }
  flush();
  return sections;
}

function readPending(kb, folder) {
  const base = path.join(kb, projects.DIR, folder);
  try { return fs.readFileSync(path.join(base, 'pending.md'), 'utf8'); }
  catch (err) { if (err.code !== 'ENOENT') throw err; }
  // 以前の、backlog/ をそのままコピーした取り込み形式。
  const items = [];
  const visit = rel => {
    const file = path.join(base, rel);
    let stat;
    try { stat = fs.lstatSync(file); } catch (err) { if (err.code === 'ENOENT') return; throw err; }
    if (stat.isSymbolicLink() || path.basename(rel).startsWith('.')) return;
    if (stat.isDirectory()) { for (const name of fs.readdirSync(file).sort()) visit(path.posix.join(rel, name)); return; }
    if (!stat.isFile() || !/\.md$/i.test(rel) || stat.size > 1024 * 1024) return;
    for (const entry of importer.pendingEntries(fs.readFileSync(file, 'utf8'))) {
      const content = importer.extract(entry.text, 'pending', rel);
      const source = rel + entry.suffix;
      items.push({ id: source, group: 'pending', title: /^#+\s+(.+)$/m.exec(content)?.[1] || path.basename(rel), content, sources: [source] });
    }
  };
  visit('backlog');
  visit('backlog.md');
  return bundle.build(items, items.map(item => item.id)).documents[0]?.content || '';
}

function list(context) {
  if (!context) return [];
  const { kb, folder, key, project, resolved = [] } = context;
  const text = readPending(kb, folder);
  const writable = resolved.filter(repo => repo.role !== 'reference');
  const seen = new Map();
  return parse(text).map(item => {
    const root = projects.chooseRepo(writable, item.body).repo?.path || (!writable.length ? kb : '');
    const seed = `${key}\n${item.source}`;
    const occurrence = seen.get(seed) || 0; seen.set(seed, occurrence + 1);
    const id = `import-${digest(`${seed}\n${occurrence}`)}`;
    return { id, name: item.title, description: `${project.name} · ${item.source}`,
      project: key, root, body: item.body,
      ...(!root ? { error: 'プロジェクトの編集で作業リポジトリのフォルダを選んでください' } : {}),
    };
  });
}

function definition(item, context) {
  const rules = projects.rulesText(context.kb, context.folder);
  const constraints = rules ? `\n\nプロジェクトの目的・方針:\n${rules}` : '';
  return {
    id: item.id, name: item.name, description: item.description, defaultRequest: item.body,
    nodes: [
      { id: 'work', label: '作業を実施', kind: 'work', deps: [],
        goal: `依頼内容に従って作業し、成果物と変更点を報告してください。\n\n{{request}}${constraints}` },
      { id: 'verify', label: '成果を検証', kind: 'verify', deps: ['work'],
        goal: '前工程の成果を独立して確認してください。依頼にある受入基準・検証条件を確認し、検証コマンドがあれば実行してください。結果と証拠を報告し、未実施・未達の条件を成功として扱わないでください。\n\n{{request}}' },
    ],
  };
}

function ensure(context, id) {
  const item = list(context).find(entry => entry.id === id);
  if (!item) throw new Error('取り込んだワークフローが見つかりません');
  return ensureItem(context, item);
}

function ensureItem(context, item) {
  const id = item.id;
  if (!item.root) throw new Error(item.error);
  // 取り込み後の編集を再生成で消さない。
  if (!fs.existsSync(flowStore.fileOf(item.root, id))) {
    const result = flowStore.save(item.root, definition(item, context), 'create');
    if (!result.saved) throw new Error(result.issues.map(issue => issue.message).join('\n'));
  }
  return { root: item.root, id, name: item.name };
}

function register(context) {
  const items = list(context);
  const registered = [], warnings = [];
  for (const item of items) {
    try {
      const checked = flowModel.preview(definition(item, context));
      if (!checked.ok) throw new Error(checked.issues.map(issue => issue.message).join('\n'));
      registered.push(ensureItem(context, item));
    } catch (err) { warnings.push(`${item.name}: ${err.message}`); }
  }
  return { registered, warnings };
}

module.exports = { parse, list, definition, ensure, register };
