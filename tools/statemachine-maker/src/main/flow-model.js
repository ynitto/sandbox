'use strict';

// agent-flow の保存形を、画面で扱える検証結果と投入 plan に変換する。
// 定義の誤りは throw せず issues に集める。ファイルや実行環境の失敗だけを IPC が throw する。

const crypto = require('crypto');
const parameters = require('./template-parameters');

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
const MAX_NODES = 64;

const KIND_INFOS = [
  ['work', '実行', '依頼された作業を進めます。', 'work', true],
  ['generate', '案を作る', '候補や下書きを作ります。', 'work', true],
  ['classify', '分類する', '入力を分類し、後続の判断材料を作ります。', 'decision', true],
  ['synthesize', 'まとめる', '複数の成果を一つに統合します。', 'aggregate', true],
  ['verify', '確かめる', '成果が条件を満たすか確認します。', 'decision', true],
  ['filter', '絞り込む', '候補を条件で絞り込みます。', 'decision', true],
  ['judge', '選ぶ', '複数の候補を比較して選びます。', 'decision', true],
  ['reduce', '集約する', '構造化された複数の結果を集約します。', 'aggregate', true],
  ['split', '分割する', '入力を実行時に複数の作業へ分けます。', 'fanout', false],
  ['map', '個別に処理', '分割された各要素を処理します。', 'fanout', true],
  ['human', '人に確認', '途中で人の承認・選択・入力を待ちます。', 'human', true],
  ['extract', '取り出す', '入力から必要な情報を取り出します。', 'work', true],
  ['retrieve', '探す', '必要な情報を検索・取得します。', 'work', true],
].map(([kind, label, description, group, dependable]) => ({
  kind, label, description, group, planner: kind !== 'human',
  constraints: { dependable, needsInteraction: kind === 'human' },
}));
const VALID_KINDS = new Set(KIND_INFOS.map((item) => item.kind));

function issue(issues, code, message, path, nodeId, level = 'error') {
  issues.push({ level, code, message, path, ...(nodeId ? { nodeId } : {}) });
}

function plainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function interactionOf(raw, issues, path, nodeId) {
  if (!plainObject(raw)) {
    issue(issues, 'interaction-required', '人に確認する工程には、確認方法と質問を設定してください', path, nodeId);
    return { mode: 'approval', prompt: '', timeout_seconds: 604800, audience: ['reviewer'] };
  }
  const mode = String(raw.mode || '');
  const prompt = String(raw.prompt || '').trim();
  const options = Array.isArray(raw.options) ? raw.options.map((v) => String(v).trim()).filter(Boolean) : [];
  const unique = [...new Set(options)];
  const timeout = raw.timeout_seconds == null ? 604800 : Number(raw.timeout_seconds);
  let invalid = false;
  if (!['approval', 'choice', 'input'].includes(mode) || !prompt) invalid = true;
  if (mode === 'choice' && (unique.length < 2 || unique.length !== options.length)) invalid = true;
  const defaultOption = String(raw.default_option || '').trim();
  if (defaultOption && (mode !== 'choice' || !unique.includes(defaultOption))) invalid = true;
  if (!Number.isFinite(timeout) || timeout <= 0) invalid = true;
  if (invalid) issue(issues, 'interaction-invalid', '確認方法の内容を見直してください', path, nodeId);
  return {
    mode: ['approval', 'choice', 'input'].includes(mode) ? mode : 'approval',
    prompt,
    ...(mode === 'choice' ? { options: unique, ...(defaultOption ? { default_option: defaultOption } : {}) } : {}),
    timeout_seconds: Number.isFinite(timeout) && timeout > 0 ? timeout : 604800,
    audience: Array.isArray(raw.audience) && raw.audience.length
      ? raw.audience.map(String).filter(Boolean) : ['reviewer'],
  };
}

function normalize(raw) {
  const src = plainObject(raw) ? raw : {};
  const issues = [];
  const id = String(src.id || '').trim();
  const name = String(src.name || '').trim();
  if (!name) issue(issues, 'name-required', '名前を入力してください', 'name');
  if (!ID_RE.test(id)) issue(issues, 'id-invalid', '保存名は英数字で始め、英数字・_・- だけで入力してください', 'id');
  const sourceNodes = Array.isArray(src.nodes) ? src.nodes : [];
  if (!sourceNodes.length) issue(issues, 'nodes-empty', '工程を1つ以上追加してください', 'nodes');
  if (sourceNodes.length > MAX_NODES) issue(issues, 'nodes-too-many', `工程は${MAX_NODES}件までです`, 'nodes');

  const seen = new Set();
  const nodes = sourceNodes.slice(0, MAX_NODES).map((value, index) => {
    const node = plainObject(value) ? value : {};
    const nodeId = String(node.id || '').trim();
    const path = `nodes[${index}]`;
    if (!nodeId) issue(issues, 'node-id-required', '工程の保存名を入力してください', `${path}.id`);
    else if (!ID_RE.test(nodeId)) issue(issues, 'id-invalid', '工程の保存名に使えない文字があります', `${path}.id`, nodeId);
    else if (seen.has(nodeId)) issue(issues, 'node-id-duplicate', `工程の保存名「${nodeId}」が重複しています`, `${path}.id`, nodeId);
    seen.add(nodeId);
    const kind = String(node.kind || 'work').trim();
    if (!VALID_KINDS.has(kind)) issue(issues, 'kind-invalid', `工程の種類「${kind}」は使えません`, `${path}.kind`, nodeId);
    const goal = String(node.goal || '').trim();
    if (!goal) issue(issues, 'goal-required', '工程で行うことを入力してください', `${path}.goal`, nodeId);
    for (const key of parameters.templateParameterKeys(goal)) {
      if (key !== 'request' && parameters.RESERVED_TEMPLATE_KEYS.has(key)) {
        issue(issues, 'parameter-reserved', `{{${key}}} はこのワークフローの入力項目には使えません`, `${path}.goal`, nodeId);
      }
    }
    let interaction;
    if (kind === 'human') interaction = interactionOf(node.interaction, issues, `${path}.interaction`, nodeId);
    else if (node.interaction != null) issue(issues, 'interaction-not-allowed', '人に確認する工程以外には確認方法を設定できません', `${path}.interaction`, nodeId);
    const normalizedKind = VALID_KINDS.has(kind) ? kind : 'work';
    return {
      id: nodeId,
      label: String(node.label || nodeId).trim() || nodeId,
      kind: normalizedKind,
      goal,
      deps: Array.isArray(node.deps) ? [...new Set(node.deps.map((dep) => String(dep).trim()).filter(Boolean))] : [],
      ...(normalizedKind !== 'human' ? { tier: 'auto' } : {}),
      ...(interaction ? { interaction } : {}),
      ...(Number.isFinite(Number(node.x)) ? { x: Number(node.x) } : {}),
      ...(Number.isFinite(Number(node.y)) ? { y: Number(node.y) } : {}),
    };
  });

  const byId = new Map(nodes.filter((node) => node.id).map((node) => [node.id, node]));
  nodes.forEach((node, index) => {
    node.deps.forEach((dep, depIndex) => {
      if (dep === node.id) issue(issues, 'dep-self', '工程を自分自身にはつなげられません', `nodes[${index}].deps[${depIndex}]`, node.id);
      else if (!byId.has(dep)) issue(issues, 'dep-unknown', `接続先「${dep}」が見つかりません`, `nodes[${index}].deps[${depIndex}]`, node.id);
      else if (byId.get(dep).kind === 'split') issue(issues, 'dep-on-split', '「分割する」の直後は実行時に自動で展開されるため、通常の工程をつなげられません', `nodes[${index}].deps[${depIndex}]`, node.id);
    });
  });
  const visiting = new Set();
  const visited = new Set();
  function visit(node) {
    if (!node || visited.has(node.id)) return;
    if (visiting.has(node.id)) {
      const index = nodes.findIndex((item) => item.id === node.id);
      issue(issues, 'dep-cycle', '工程のつながりが循環しています', `nodes[${Math.max(0, index)}].deps`, node.id);
      return;
    }
    visiting.add(node.id);
    node.deps.forEach((dep) => visit(byId.get(dep)));
    visiting.delete(node.id);
    visited.add(node.id);
  }
  nodes.forEach(visit);
  const used = new Set(nodes.flatMap((node) => node.deps));
  const now = new Date().toISOString();
  return {
    workflow: {
      version: 2,
      id,
      name,
      description: String(src.description || '').trim(),
      purpose: 'implementation',
      entry: nodes.filter((node) => !node.deps.length).map((node) => node.id).filter(Boolean),
      exit: nodes.filter((node) => !used.has(node.id)).map((node) => node.id).filter(Boolean),
      nodes,
      createdAt: String(src.createdAt || now),
      updatedAt: String(src.updatedAt || now),
    },
    issues,
  };
}

function definition(workflow) {
  return {
    version: 2,
    id: workflow.id,
    name: workflow.name,
    description: workflow.description,
    purpose: 'implementation',
    entry: workflow.entry,
    exit: workflow.exit,
    nodes: workflow.nodes.map((node) => ({
      id: node.id, label: node.label, kind: node.kind, goal: node.goal, deps: node.deps,
      ...(node.kind !== 'human' ? { tier: 'auto' } : {}),
      ...(node.interaction ? { interaction: node.interaction } : {}),
    })),
  };
}

function digest(workflow) {
  return crypto.createHash('sha256').update(JSON.stringify(definition(workflow)), 'utf8').digest('hex').slice(0, 16);
}

function planOf(workflow, values = {}) {
  return {
    name: workflow.name,
    nodes: workflow.nodes.map((node) => ({
      id: node.id,
      goal: parameters.applyParameters(node.goal, values),
      kind: node.kind,
      deps: [...node.deps],
      ...(node.interaction ? { interaction: node.interaction } : {}),
    })),
  };
}

function preview(raw, request, rawParameters) {
  const normalized = normalize(raw);
  const workflow = normalized.workflow;
  const issues = [...normalized.issues];
  const requestText = String(request || '');
  const keys = parameters.inputParameterKeys(requestText, ...workflow.nodes.map((node) => node.goal));
  const hasInputs = request !== undefined || rawParameters !== undefined;
  const supplied = plainObject(rawParameters) ? rawParameters : {};
  const values = {};
  if (hasInputs) {
    for (const key of Object.keys(supplied)) {
      if (!keys.includes(key)) issue(issues, 'goal-has-unfilled-parameter', `未定義の入力項目「${key}」があります`, 'parameters');
      else values[key] = String(supplied[key] == null ? '' : supplied[key]).trim();
    }
    for (const key of keys) {
      if (!values[key]) issue(issues, 'goal-has-unfilled-parameter', `入力項目「${key}」を入力してください`, 'parameters');
    }
  }
  const ok = !issues.some((item) => item.level === 'error');
  return {
    ok,
    issues,
    workflow: ok ? workflow : null,
    draft: workflow,
    parameterKeys: keys,
    plan: ok ? planOf(workflow, values) : null,
    digest: digest(workflow),
  };
}

module.exports = { ID_RE, MAX_NODES, KIND_INFOS, VALID_KINDS, normalize, preview, planOf, digest, definition };
