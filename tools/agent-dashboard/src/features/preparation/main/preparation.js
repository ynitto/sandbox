'use strict';

// Dashboard が設計runと実装runの間で保持する「作業準備」の純粋なドメイン契約。
// 実行エンジンには状態を持ち込まず、経路・材料・handoffだけをここで管理する。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { agentHomeSubdir } = require('../../../base/main/agent-home');
// 「実行できる設計書か」の判定は design-contract が唯一の実装（設計セッションと同じ判定を使う）。
const designContract = require('../../../base/main/design-contract');

// 持ち込みの Markdown は言い換え見出し（狙い・スコープ・完了条件…）も同じ節として数える。
function isCompleteDocument(text) {
  return !designContract.missingSections(text, true).length;
}

// 設計 run の成果はこちらの判定を通す。必須節に加えて節内の必須項目（既定契約なら
// 「変更対象の強制レイヤー」）まで揃っていないと実装へは渡さない（文言でしか守られて
// いない契約を実装 run へ流さない）。契約は項目が固定した設計フロー snapshot の宣言
// （design.flow.definition.contract）を使い、無ければ既定契約で判定する。
function designFlowContract(design) {
  const definition = design && design.flow && design.flow.definition;
  return (definition && definition.contract) || null;
}

function isCompleteDesignDocument(text, contract = null) {
  return !designContract.documentIssues(text, false, contract).length;
}

function designDocumentIssues(text, contract = null) {
  return designContract.documentIssues(text, false, contract);
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
const DESIGN_SCOPES = new Set(['user', 'repository', 'builtin']);

function cloneValue(value) {
  if (!value || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(cloneValue);
  return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, cloneValue(nested)]));
}

function normalizeDefinitionNode(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = String(raw.id || '').trim();
  if (!id) return null;
  const node = {
    id,
    goal: String(raw.goal || '').trim(),
    kind: String(raw.kind || 'work').trim() || 'work',
    deps: (Array.isArray(raw.deps) ? raw.deps : []).map(String),
  };
  if (raw.tier) node.tier = String(raw.tier).trim();
  if (raw.interaction && typeof raw.interaction === 'object') node.interaction = cloneValue(raw.interaction);
  if (raw.method && typeof raw.method === 'object') node.method = cloneValue(raw.method);
  if (raw.continuation) node.continuation = String(raw.continuation).trim();
  return node;
}

function normalizeDesignDefinition(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.nodes)) return null;
  const nodes = raw.nodes.map(normalizeDefinitionNode);
  if (!nodes.length || nodes.some((node) => !node)) return null;
  // 設計契約の宣言は snapshot の一部として保持する。壊れた宣言は捨てて既定契約へ落とす。
  let contract = null;
  if (raw.contract) {
    try {
      contract = designContract.normalizeContract(raw.contract);
    } catch { contract = null; }
  }
  return {
    version: Number(raw.version) || 2,
    purpose: String(raw.purpose || 'implementation').trim() || 'implementation',
    libraryVisibility: String(raw.libraryVisibility || 'library').trim() || 'library',
    ...(contract ? { contract } : {}),
    entry: (Array.isArray(raw.entry) ? raw.entry : []).map(String),
    exit: (Array.isArray(raw.exit) ? raw.exit : []).map(String),
    nodes,
  };
}

// design.flow は renderer の選択値ではなく、main が解決した workflow の snapshot だけを保存する。
function normalizeDesignFlow(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = String(raw.id || '').trim();
  const originRaw = raw.origin && typeof raw.origin === 'object' ? raw.origin : {};
  const scope = String(originRaw.scope || '').trim();
  const repository = String(originRaw.repository || '').trim();
  const digest = String(raw.digest || '').trim();
  const definition = normalizeDesignDefinition(raw.definition);
  if (!id || !DESIGN_SCOPES.has(scope) || !digest || !definition) return null;
  if (scope === 'repository' ? !repository : repository) return null;
  return {
    version: Number(raw.version) || 1,
    id,
    name: String(raw.name || id).trim() || id,
    origin: { scope, repository },
    digest,
    definition,
  };
}

function normalizeDesign(raw, flow = null) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const normalizedFlow = normalizeDesignFlow(flow || value.flow);
  return {
    sessionId: String(value.sessionId || ''),
    document: String(value.document || ''),
    runIds: [...new Set((Array.isArray(value.runIds) ? value.runIds : []).map(String).filter(Boolean))],
    ...(normalizedFlow ? { flow: normalizedFlow } : {}),
  };
}

function digestDefinition(definition) {
  return `sha256:${crypto.createHash('sha256').update(JSON.stringify(definition)).digest('hex')}`;
}

function builtinDesignFlowSnapshot(mode) {
  const id = mode === 'auto' ? 'design-auto' : 'design-interactive';
  const candidates = [
    process.resourcesPath && path.join(process.resourcesPath, 'workflows', `${id}.json`),
    path.resolve(__dirname, '../../../../../../workflows', `${id}.json`),
  ].filter(Boolean);
  for (const file of candidates) {
    try {
      const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
      const definition = normalizeDesignDefinition(workflow);
      if (!definition) continue;
      return normalizeDesignFlow({
        version: 1,
        id: workflow.id || id,
        name: workflow.name || id,
        origin: { scope: 'builtin', repository: '' },
        digest: digestDefinition(definition),
        definition,
      });
    } catch (error) {
      if (!error || error.code !== 'ENOENT') continue;
    }
  }
  return null;
}

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
  const design = route === 'agent-design' ? normalizeDesign(raw.design) : normalizeDesign();
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
    design,
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
  const packageFlow = normalizeDesignFlow(raw.design && raw.design.flow);
  const now = new Date().toISOString();
  const makeCandidate = (rawCandidate) => {
    const candidate = rawCandidate && typeof rawCandidate === 'object' ? rawCandidate : {};
    const candidateMaterials = [...materials, ...(Array.isArray(candidate.materials) ? candidate.materials : [])];
    const recommendation = recommendRoute({ goal: String(candidate.goal || ''), materials: candidateMaterials });
    const route = ROUTES.has(candidate.route) ? candidate.route : recommendation.route;
    const candidateFlow = route === 'agent-design'
      ? normalizeDesignFlow(candidate.design && candidate.design.flow) || packageFlow
      : null;
    return createItem({
      ...candidate,
      target: 'project',
      projectDir,
      packageId: id,
      taskSpec: candidate,
      designMode: candidate.designMode || raw.designMode,
      designAssignments: candidate.designAssignments || raw.designAssignments,
      materials: candidateMaterials,
      ...(candidateFlow ? { design: { ...(candidate.design || {}), flow: candidateFlow } } : { design: undefined }),
    });
  };
  return {
    version: 1,
    id,
    target: 'project',
    projectDir,
    title: String(raw.title || goal.slice(0, 80)),
    goal,
    materials,
    ...(packageFlow ? { design: { flow: packageFlow } } : {}),
    items: candidates.map(makeCandidate),
    createdAt: String(raw.createdAt || now),
    updatedAt: now,
  };
}

function canHandoff(item) {
  if (!item || item.phase !== 'implementation-ready') return false;
  return item.route !== 'agent-design'
    || isCompleteDesignDocument(item.design && item.design.document,
      designFlowContract(item.design));
}

function completeDesign(item, result = {}) {
  if (!item || item.route !== 'agent-design') throw new Error('エージェント設計の項目ではありません');
  const document = String(result.document || '').trim();
  if (!document) throw new Error('設計結果は必須です');
  const contract = designFlowContract(item.design);
  if (!isCompleteDesignDocument(document, contract)) {
    throw new Error(`設計結果に必須項目が不足しています: ${designDocumentIssues(document, contract).join('、')}`);
  }
  const currentDesign = normalizeDesign(item.design);
  const resultMaterialId = `design-result:${item.id}`;
  const design = {
    ...currentDesign,
    sessionId: String(result.sessionId || currentDesign.sessionId || ''),
    document,
    runIds: [...new Set([
      ...currentDesign.runIds,
      ...(Array.isArray(result.runIds) ? result.runIds : []).map(String).filter(Boolean),
    ])],
  };
  return {
    ...item,
    phase: 'implementation-ready',
    materials: normalizeMaterials([
      ...(item.materials || []).filter((material) => material && material.id !== resultMaterialId),
      {
        id: resultMaterialId,
        kind: 'design-result',
        name: '設計結果.md',
        content: document,
      },
    ]),
    design,
    updatedAt: new Date().toISOString(),
  };
}

function startDesign(item, result = {}) {
  if (!item || item.route !== 'agent-design') throw new Error('設計runを使う経路ではありません');
  const sessionId = String(result.sessionId || '').trim();
  const runId = String(result.runId || '').trim();
  if (!sessionId || !runId) throw new Error('設計セッションとrun IDは必須です');
  const storedFlow = normalizeDesignFlow(item.design && item.design.flow);
  const suppliedFlow = normalizeDesignFlow(result.designFlow)
    || normalizeDesignFlow(result.flow)
    || normalizeDesignFlow(result.design && result.design.flow);
  const flow = storedFlow || suppliedFlow || builtinDesignFlowSnapshot(item.designMode);
  return {
    ...item,
    phase: 'designing',
    design: {
      ...normalizeDesign(item.design, flow),
      sessionId,
      runIds: [...new Set([...normalizeDesign(item.design).runIds, runId])],
      ...(flow ? { flow } : {}),
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
  const next = {
    ...item,
    design: item.route === 'agent-design' ? normalizeDesign(item.design) : normalizeDesign(),
    updatedAt: new Date().toISOString(),
  };
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
  const packageFlow = normalizeDesignFlow(package_.design && package_.design.flow);
  const next = {
    ...package_,
    ...(packageFlow ? { design: { flow: packageFlow } } : package_.design ? { design: {} } : {}),
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
  recommendRoute, normalizeMaterials, normalizeDesignAssignments, normalizeDesignFlow,
  isCompleteDesignDocument, designDocumentIssues, designFlowContract,
  createItem, createPackage,
  canHandoff, startDesign, completeDesign,
  recordHandoff,
  implementationRequest, resolveDir, saveItem, getItem, removeItem, listItems, savePackage,
};
