'use strict';

// Dashboard が設計runと実装runの間で保持する「作業準備」の純粋なドメイン契約。
// 実行エンジンには状態を持ち込まず、経路・材料・handoffだけをここで管理する。

const fs = require('fs');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');

const REQUIRED_SECTIONS = [
  ['目的', '狙い'],
  ['変更対象', '対象', 'スコープ'],
  ['受入基準', '完了条件'],
  ['検証方法', '検証', 'テスト方法'],
];

function hasHeading(text, names) {
  return String(text || '').split(/\r?\n/).some((line) => {
    const heading = line.match(/^#{1,6}\s*(.+?)\s*$/);
    return heading && names.some((name) => heading[1] === name);
  });
}

function isCompleteDocument(text) {
  return REQUIRED_SECTIONS.every((names) => hasHeading(text, names));
}

function recommendRoute({ goal, materials } = {}) {
  const external = (Array.isArray(materials) ? materials : [])
    .find((item) => item && ['document', 'design-result'].includes(item.kind)
      && isCompleteDocument(item.content));
  if (external) {
    return { route: 'external-design', reasons: [`${external.name || '設計書'}をそのまま利用できます`], warnings: [] };
  }
  const complete = isCompleteDocument(goal);
  if (complete) {
    return { route: 'direct', reasons: ['実装に必要な項目が揃っています'], warnings: [] };
  }
  return {
    route: 'agent-design',
    reasons: ['目的・変更対象・受入基準・検証方法を設計で具体化します'],
    warnings: [],
  };
}

function normalizeMaterials(raw) {
  const seen = new Set();
  return (Array.isArray(raw) ? raw : []).flatMap((item, index) => {
    if (!item || typeof item !== 'object') return [];
    const kind = String(item.kind || 'document').trim() || 'document';
    const name = String(item.name || `${kind}-${index + 1}`).trim() || `${kind}-${index + 1}`;
    const id = String(item.id || `${kind}:${name}`).trim();
    const content = String(item.content || '').trim();
    if (!id || !content || seen.has(id)) return [];
    seen.add(id);
    const selected = (Array.isArray(item.selectedFor) ? item.selectedFor : ['design', 'implementation'])
      .filter((phase) => ['design', 'implementation'].includes(phase));
    return [{
      id, kind, name, content,
      sourcePath: String(item.sourcePath || ''),
      sourceHash: String(item.sourceHash || ''),
      selectedFor: [...new Set(selected)],
    }];
  });
}

const ROUTES = new Set(['agent-design', 'external-design', 'direct']);
const TARGETS = new Set(['workflow', 'project']);
const DESIGN_MODES = new Set(['interactive', 'auto']);

// 設計フローのノードへ人が固定したエージェント・モデル（{ nodeId: {tier, agent_cli, model} }）。
// ここでは形だけを整えて保存する——tier の適格性と候補の実在は、設計runを組む adhoc 側が
// 実行時点の宣言（profiles.json）で検証する。
function normalizeDesignAssignments(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const out = {};
  for (const [nodeId, value] of Object.entries(raw)) {
    if (!value || typeof value !== 'object') continue;
    const tier = String(value.tier || '').trim();
    const agentCli = String(value.agent_cli || '').trim();
    if (!tier || !agentCli) continue;
    out[String(nodeId)] = { tier, agent_cli: agentCli, model: String(value.model || '').trim() };
  }
  return Object.keys(out).length ? out : null;
}

function createItem(raw = {}) {
  const title = String(raw.title || '').trim();
  const goal = String(raw.goal || '').trim();
  if (!title) throw new Error('仕事名は必須です');
  if (!goal) throw new Error('やりたいことは必須です');
  const target = TARGETS.has(raw.target) ? raw.target : 'workflow';
  const materials = normalizeMaterials(raw.materials);
  const routeRecommendation = recommendRoute({ goal, materials });
  const route = ROUTES.has(raw.route) ? raw.route : routeRecommendation.route;
  const now = new Date().toISOString();
  return {
    version: 1,
    id: String(raw.id || `prep-${Date.now().toString(36)}-${Math.floor(1000 + Math.random() * 9000)}`),
    target,
    projectDir: String(raw.projectDir || ''),
    cwd: String(raw.cwd || ''),
    packageId: String(raw.packageId || ''),
    title,
    goal,
    route,
    routeRecommendation,
    materials,
    designMode: DESIGN_MODES.has(raw.designMode) ? raw.designMode : 'interactive',
    designAssignments: normalizeDesignAssignments(raw.designAssignments),
    taskSpec: raw.taskSpec && typeof raw.taskSpec === 'object' ? { ...raw.taskSpec } : null,
    phase: route === 'agent-design' ? 'design-ready' : 'implementation-ready',
    design: { sessionId: '', document: '', runIds: [] },
    handoff: { taskId: '', implementationRunIds: [] },
    createdAt: String(raw.createdAt || now),
    updatedAt: now,
  };
}

function createPackage(raw = {}) {
  const projectDir = String(raw.projectDir || '').trim();
  const goal = String(raw.goal || '').trim();
  if (!projectDir) throw new Error('プロジェクトは必須です');
  if (!goal) throw new Error('やりたいことは必須です');
  const candidates = Array.isArray(raw.candidates) ? raw.candidates : [];
  if (!candidates.length) throw new Error('バックログ候補は1件以上必要です');
  const id = String(raw.id || `pkg-${Date.now().toString(36)}-${Math.floor(1000 + Math.random() * 9000)}`);
  const materials = normalizeMaterials(raw.materials);
  const now = new Date().toISOString();
  return {
    version: 1,
    id,
    target: 'project',
    projectDir,
    title: String(raw.title || goal.slice(0, 80)),
    goal,
    materials,
    items: candidates.map((candidate) => createItem({
      ...candidate,
      target: 'project',
      projectDir,
      packageId: id,
      taskSpec: candidate,
      designMode: candidate.designMode || raw.designMode,
      designAssignments: candidate.designAssignments || raw.designAssignments,
      materials: [...materials, ...(Array.isArray(candidate.materials) ? candidate.materials : [])],
    })),
    createdAt: String(raw.createdAt || now),
    updatedAt: now,
  };
}

function canHandoff(item) {
  return !!item && item.phase === 'implementation-ready';
}

function completeDesign(item, result = {}) {
  if (!item || item.route !== 'agent-design') throw new Error('エージェント設計の項目ではありません');
  const document = String(result.document || '').trim();
  if (!document) throw new Error('設計結果は必須です');
  const design = {
    sessionId: String(result.sessionId || ''),
    document,
    runIds: (Array.isArray(result.runIds) ? result.runIds : []).map(String).filter(Boolean),
  };
  return {
    ...item,
    phase: 'implementation-ready',
    materials: normalizeMaterials([...(item.materials || []), {
      id: `design-result:${item.id}`,
      kind: 'design-result',
      name: '設計結果.md',
      content: document,
    }]),
    design,
    updatedAt: new Date().toISOString(),
  };
}

function startDesign(item, result = {}) {
  if (!item || item.route !== 'agent-design') throw new Error('設計runを使う経路ではありません');
  const sessionId = String(result.sessionId || '').trim();
  const runId = String(result.runId || '').trim();
  if (!sessionId || !runId) throw new Error('設計セッションとrun IDは必須です');
  return {
    ...item,
    phase: 'designing',
    design: {
      ...(item.design || {}),
      sessionId,
      runIds: [...new Set([...(item.design && item.design.runIds || []), runId])],
    },
    updatedAt: new Date().toISOString(),
  };
}

function recordHandoff(item, result = {}) {
  if (!canHandoff(item)) throw new Error('実装準備が完了していません');
  const runId = String(result.runId || '').trim();
  const taskId = String(result.taskId || '').trim();
  if (!runId && !taskId) throw new Error('実装先のIDは必須です');
  return {
    ...item,
    phase: runId ? 'implementing' : 'queued',
    handoff: {
      taskId: taskId || String(item.handoff && item.handoff.taskId || ''),
      implementationRunIds: [...new Set([
        ...(item.handoff && item.handoff.implementationRunIds || []),
        ...(runId ? [runId] : []),
      ])],
    },
    updatedAt: new Date().toISOString(),
  };
}

function resolveDir(config = {}) {
  return String(config.preparationDir || config.adhocFlow && config.adhocFlow.preparationDir || '').trim()
    || agentHomeSubdir('preparation');
}

function itemFile(config, id) {
  const clean = String(id || '').trim();
  if (!/^prep-[a-zA-Z0-9-]{1,90}$/.test(clean)) throw new Error(`作業準備IDが不正です: ${clean || '(空)'}`);
  return path.join(resolveDir(config), 'items', `${clean}.json`);
}

function writeAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, file);
}

function saveItem(config, item) {
  if (!item || typeof item !== 'object') throw new Error('作業準備項目が不正です');
  const next = { ...item, updatedAt: new Date().toISOString() };
  writeAtomic(itemFile(config, next.id), next);
  return next;
}

function getItem(config, id) {
  try { return JSON.parse(fs.readFileSync(itemFile(config, id), 'utf8')); }
  catch (error) {
    if (error && error.code === 'ENOENT') return null;
    throw error;
  }
}

function removeItem(config, id) {
  const file = itemFile(config, id);
  if (!fs.existsSync(file)) return false;
  fs.rmSync(file);
  return true;
}

function listItems(config, filters = {}) {
  const dir = path.join(resolveDir(config), 'items');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((name) => name.endsWith('.json')).flatMap((name) => {
    try { return [JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'))]; }
    catch { return []; }
  }).filter((item) => !filters.target || item.target === filters.target)
    .filter((item) => !filters.projectDir || item.projectDir === filters.projectDir)
    .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
}

function savePackage(config, package_) {
  if (!package_ || !/^pkg-[a-zA-Z0-9-]{1,90}$/.test(String(package_.id || ''))) {
    throw new Error('準備パッケージが不正です');
  }
  const items = Array.isArray(package_.items) ? package_.items : [];
  for (const item of items) saveItem(config, item);
  const next = {
    ...package_,
    items: undefined,
    itemIds: items.map((item) => item.id),
    updatedAt: new Date().toISOString(),
  };
  const file = path.join(resolveDir(config), 'packages', `${next.id}.json`);
  writeAtomic(file, next);
  return next;
}

function implementationRequest(item) {
  const parts = [String(item && item.goal || '').trim()].filter(Boolean);
  const materials = (item && Array.isArray(item.materials) ? item.materials : [])
    .filter((material) => Array.isArray(material.selectedFor) && material.selectedFor.includes('implementation'));
  if (materials.length) {
    parts.push(`## 実装材料\n${materials.map((material) =>
      `### ${material.kind}: ${material.name}\n${material.content}`).join('\n\n')}`);
  }
  return parts.join('\n\n');
}

module.exports = {
  recommendRoute, normalizeMaterials, normalizeDesignAssignments, createItem, createPackage,
  canHandoff, startDesign, completeDesign,
  recordHandoff,
  implementationRequest, resolveDir, saveItem, getItem, removeItem, listItems, savePackage,
};
