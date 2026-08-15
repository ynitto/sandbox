'use strict';

// ワークフロー実行。既存の agent-flow 投入・監視契約を使い、次だけを担当する:
//   - 投入 … <bus>/inbox/<run-id>.json（submit_request 契約。plan フィールドで
//     ユーザー定義フロー＝ビルダーの成果物を運ぶ）
//   - 書込先 … 選択した Git cwd を workspace として固定（成果は af/<run-id> branch）
//   - 保存 … ~/.agents/workflows/<id>.json（ユーザー共通）。リポジトリ内の
//     <repo>/.agents/workflows/<id>.json は読むだけで、通常の Git 運用が配布する
//     （agent-project の <repo>/.agents/agent-project.yaml と同じ語彙。ルートは
//     ホーム（~）または各 git ルート）
//   - 手法 … run 専用の AGENT_TUNING_DIR に agent-tuning 契約のスナップショットを複製
//     （S26 の「参照でなく複製」と同じ。source: methods/<id>@<hash> で乖離検出可能）
// 実行系は agent-flow run そのもの（新しい実行系・状態ファイルは作らない）。
// run の読み取りは agent-project feature の flow.js（バスパーサ）をそのまま再利用する
// （C7: バスの読み手を 2 実装にしない）。
//
// バックログの状態は直接触らない。バックログ側のフロー選択は commands/ の flow
// スナップショットを agent-project が task sidecar へ永続化する。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { agentHomeSubdir } = require('../../../base/main/agent-home');
const exec = require('../../routines/main/exec');
const flow = require('../../agent-project/main/flow');
const tuning = require('../../orchestration/main/tuning');
const profiles = require('../../orchestration/main/profiles');
const agents = require('../../orchestration/main/agents');
const control = require('../../orchestration/main/control');
const flowTiers = require('../../orchestration/main/flow-tiers');

const SUBMITTER = 'agent-dashboard-adhoc';
const NODE_KINDS = ['work', 'generate', 'classify', 'synthesize', 'verify', 'filter', 'judge',
  'reduce', 'split', 'map', 'human', 'extract', 'retrieve'];
const EXECUTION_ROLES = ['planner', 'evaluator', 'worker', 'verify'];
const WORKFLOW_PURPOSES = ['implementation', 'design'];
const DESIGN_TERMINAL_KINDS = new Set(['work', 'synthesize']);

function cfgOf(config) {
  return (config && config.adhocFlow) || {};
}

function resolveBusDir(config) {
  const c = cfgOf(config);
  return String(c.busDir || '').trim() || agentHomeSubdir('flow', 'bus');
}

function writeJsonAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2)}`;
  try {
    fs.writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`);
    fs.renameSync(tmp, file);
  } catch (error) {
    try { fs.rmSync(tmp, { force: true }); } catch { /* 元のエラーを優先する */ }
    throw error;
  }
}

// --- ユーザー共通ワークフロー -------------------------------------------------

function resolveWorkflowDir(config) {
  return String(cfgOf(config).workflowDir || '').trim() || agentHomeSubdir('workflows');
}

function repositoryRoot(cwd) {
  let dir = path.resolve(String(cwd || '').trim() || '.');
  try {
    if (fs.statSync(dir).isFile()) dir = path.dirname(dir);
  } catch {
    return '';
  }
  while (true) {
    if (fs.existsSync(path.join(dir, '.git'))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return '';
    dir = parent;
  }
}

function repositoryWorkflowDir(cwd) {
  const root = repositoryRoot(cwd);
  return root ? path.join(root, '.agents', 'workflows') : '';
}

function repositoryMethodsDir(cwd) {
  const root = repositoryRoot(cwd);
  return root ? path.join(root, '.agents', 'methods') : '';
}

function registeredRepositoryRoot(config, cwd) {
  const root = repositoryRoot(cwd);
  if (!root) return '';
  const engine = require('../../agent-project/main/engine');
  const registered = engine.projectRoots(config).map((dir) => path.resolve(String(dir)));
  return registered.includes(path.resolve(root)) ? root : '';
}

// 同梱フローの置き場。手法カタログ（tuning.resolveMethodsDir）と同じ配布規則で解決する
// ——パッケージ版は extraResources、開発起動はリポジトリ直下の workflows/。
function resolveBuiltinWorkflowDir(config) {
  const explicit = String(cfgOf(config).builtinWorkflowDir || '').trim();
  if (explicit) return explicit;
  const packaged = process.resourcesPath && path.join(process.resourcesPath, 'workflows');
  if (packaged && fs.existsSync(packaged)) return packaged;
  return path.resolve(__dirname, '../../../../../../workflows');
}

function workflowDirs(config, cwd = '') {
  const registered = registeredRepositoryRoot(config, cwd);
  const repository = registered ? path.join(registered, '.agents', 'workflows') : '';
  // 同じ id は先に並ぶスコープが勝つ。同梱を最後に置き、ユーザーが同 id を保存すれば
  // 同梱版を隠せるようにする（手法カタログと同じ上書き規則）。
  return [
    ...(repository ? [{ scope: 'repository', dir: repository, repository: registered }] : []),
    { scope: 'user', dir: resolveWorkflowDir(config), repository: '' },
    { scope: 'builtin', dir: resolveBuiltinWorkflowDir(config), repository: '' },
  ];
}

const READONLY_SCOPES = { repository: 'リポジトリ共有フロー', builtin: '同梱フロー' };

function workflowId(raw) {
  const id = String(raw || '').trim();
  if (id && !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(id)) {
    throw new Error('フロー id が不正です');
  }
  return id || `workflow-${Date.now()}`;
}

function normalizeExpectedPurpose(raw) {
  if (raw == null || String(raw).trim() === '') return '';
  const purpose = String(raw).trim();
  if (!WORKFLOW_PURPOSES.includes(purpose)) throw new Error(`フローのpurposeが不正です: ${purpose}`);
  return purpose;
}

function normalizeInteraction(raw) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const mode = String(value.mode || 'approval').trim();
  const prompt = String(value.prompt || '').trim();
  if (!['approval', 'choice', 'input'].includes(mode) || !prompt) {
    throw new Error('human ノードには有効な mode と確認内容が必要です');
  }
  const timeout = Number(value.timeout_seconds == null ? 604800 : value.timeout_seconds);
  if (!Number.isInteger(timeout) || timeout <= 0) throw new Error('human の期限は正数で指定してください');
  const audience = (Array.isArray(value.audience) ? value.audience : ['reviewer'])
    .map(String).map((item) => item.trim()).filter(Boolean);
  if (!audience.length) throw new Error('human の対象グループを指定してください');
  const interaction = { mode, prompt, audience, timeout_seconds: timeout };
  if (mode === 'choice') {
    const options = (Array.isArray(value.options) ? value.options : [])
      .map(String).map((item) => item.trim()).filter(Boolean);
    if (options.length < 2 || new Set(options).size !== options.length) {
      throw new Error('選択式には重複しない選択肢が2件以上必要です');
    }
    const defaultOption = String(value.default_option || '').trim();
    if (defaultOption && !options.includes(defaultOption)) throw new Error('既定値は選択肢から選んでください');
    interaction.options = options;
    if (defaultOption) interaction.default_option = defaultOption;
  }
  return interaction;
}

function normalizeWorkflow(raw, options = {}) {
  if (!raw || typeof raw !== 'object') throw new Error('フローが不正です');
  const name = String(raw.name || '').trim();
  if (!name) throw new Error('フロー名は必須です');
  const rawPurpose = String(raw.purpose || '').trim();
  const purpose = rawPurpose ? normalizeExpectedPurpose(rawPurpose) : 'implementation';
  const expectedPurpose = normalizeExpectedPurpose(options.purpose);
  if (expectedPurpose && expectedPurpose !== purpose) {
    throw new Error(`フローのpurposeが不一致です: ${raw.id || '(未指定)'} は ${purpose} 用です`);
  }
  const seen = new Set();
  const nodes = (Array.isArray(raw.nodes) ? raw.nodes : []).map((n) => {
    const id = String((n && n.id) || '').trim();
    const goal = String((n && n.goal) || '').trim();
    const rawTier = String((n && n.tier) || '').trim();
    const tier = rawTier === '自動' ? 'auto' : rawTier;
    const kind = String((n && n.kind) || 'work').trim() || 'work';
    if (!NODE_KINDS.includes(kind)) throw new Error(`ノード種別が不正です: ${kind}`);
    if (purpose === 'design' && ['human', 'split'].includes(kind)) {
      throw new Error(`設計フローでは ${kind} ノードを使用できません`);
    }
    if (!id || !goal || (kind !== 'human' && !tier)) throw new Error('ノードには id・内容・tier が必要です');
    if (seen.has(id)) throw new Error(`ノード id が重複しています: ${id}`);
    seen.add(id);
    const rawMethod = n.method && typeof n.method === 'object' ? n.method : null;
    const method = rawMethod && String(rawMethod.id || '').trim() && String(rawMethod.text || '').trim()
      ? {
        id: String(rawMethod.id).trim(),
        description: String(rawMethod.description || '').trim(),
        role: String(rawMethod.role || 'worker').trim() || 'worker',
        text: String(rawMethod.text).trim(),
        source: String(rawMethod.source || '').trim(),
      } : null;
    return {
      id,
      label: String(n.label || id).trim() || id,
      goal,
      kind,
      ...(kind === 'human' ? { interaction: normalizeInteraction(n.interaction) } : { tier }),
      deps: (Array.isArray(n.deps) ? n.deps : []).map((d) => String(d).trim()).filter(Boolean),
      x: Number.isFinite(Number(n.x)) ? Number(n.x) : 40,
      y: Number.isFinite(Number(n.y)) ? Number(n.y) : 40,
      ...(method && kind !== 'human' ? { method } : {}),
      ...((kind === 'classify' && n.continuation === 'route')
        || (kind === 'verify' && n.continuation === 'retry')
        ? { continuation: n.continuation } : {}),
    };
  });
  if (!nodes.length) throw new Error('ノードを1つ以上追加してください');
  for (const n of nodes) {
    if (n.deps.some((d) => !seen.has(d))) throw new Error(`${n.id} の接続先が見つかりません`);
    if (n.deps.includes(n.id)) throw new Error(`${n.id} を自分自身には接続できません`);
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const n of nodes) {
    const split = n.deps.find((d) => byId.get(d).kind === 'split');
    if (split) throw new Error(`${split} は split のため通常ノードへ接続できません`);
  }
  const visiting = new Set();
  const done = new Set();
  function visit(id) {
    if (visiting.has(id)) throw new Error('フローに循環があります');
    if (done.has(id)) return;
    visiting.add(id);
    byId.get(id).deps.forEach(visit);
    visiting.delete(id);
    done.add(id);
  }
  nodes.forEach((n) => visit(n.id));
  const roots = nodes.filter((n) => !n.deps.length).map((n) => n.id);
  const used = new Set(nodes.flatMap((n) => n.deps));
  const leaves = nodes.filter((n) => !used.has(n.id)).map((n) => n.id);
  const explicit = Number(raw.version) >= 2;
  const endpoints = (value) => (Array.isArray(value)
    ? value.map((id) => String(id).trim()).filter(Boolean) : []);
  const entry = explicit ? endpoints(raw.entry) : roots;
  const exit = explicit ? endpoints(raw.exit) : leaves;
  const sameIds = (a, b) => a.length === new Set(a).size
    && a.length === b.length && a.every((id) => b.includes(id));
  if (purpose === 'design' && exit.length !== 1) {
    throw new Error('設計フローの終了は1つだけ指定してください');
  }
  if (!sameIds(entry, roots)) throw new Error('開始はすべてのルートノードへ接続してください');
  if (!sameIds(exit, leaves)) throw new Error('すべての末端ノードを終了へ接続してください');
  if (purpose === 'design') {
    const terminal = byId.get(exit[0]);
    if (!terminal || !DESIGN_TERMINAL_KINDS.has(terminal.kind)) {
      throw new Error(`設計フローの終端kindは work または synthesize が必要です: ${terminal && terminal.kind}`);
    }
    const dependents = new Map(nodes.map((node) => [node.id, []]));
    nodes.forEach((node) => node.deps.forEach((dep) => dependents.get(dep).push(node.id)));
    const visitFrom = (starts, next) => {
      const reached = new Set();
      const pending = [...starts];
      while (pending.length) {
        const id = pending.pop();
        if (reached.has(id)) continue;
        reached.add(id);
        pending.push(...next(id));
      }
      return reached;
    };
    const fromEntry = visitFrom(entry, (id) => dependents.get(id) || []);
    if (fromEntry.size !== nodes.length) {
      throw new Error('設計フローの全ノードが開始から到達可能である必要があります');
    }
    const toExit = visitFrom(exit, (id) => byId.get(id).deps);
    if (toExit.size !== nodes.length) {
      throw new Error('設計フローの全ノードが終了へ到達可能である必要があります');
    }
  }
  const now = new Date().toISOString();
  return {
    version: 2,
    id: workflowId(raw.id),
    name,
    description: String(raw.description || '').trim(),
    purpose,
    libraryVisibility: raw.libraryVisibility === 'internal' ? 'internal' : 'library',
    entry,
    exit,
    nodes,
    createdAt: String(raw.createdAt || now),
    updatedAt: String(raw.updatedAt || now),
  };
}

// digest の対象は表示位置や作成日時を含まない v2 定義の正規形に限定する。
// IPC 側の設計スナップショットと同じキー順・語彙にして、同じ定義から常に同じ値を得る。
function workflowDefinition(raw) {
  const workflow = normalizeWorkflow(raw);
  return {
    version: workflow.version,
    purpose: workflow.purpose,
    libraryVisibility: workflow.libraryVisibility,
    entry: workflow.entry.map(String),
    exit: workflow.exit.map(String),
    nodes: workflow.nodes.map((node) => {
      const out = {
        id: String(node.id),
        goal: String(node.goal || ''),
        kind: String(node.kind || 'work'),
        deps: Array.isArray(node.deps) ? node.deps.map(String) : [],
      };
      if (node.tier) out.tier = String(node.tier);
      if (node.interaction && typeof node.interaction === 'object') out.interaction = node.interaction;
      if (node.method && typeof node.method === 'object') out.method = node.method;
      if (node.continuation) out.continuation = String(node.continuation);
      return out;
    }),
  };
}

function digestDefinition(definition) {
  return `sha256:${crypto.createHash('sha256')
    .update(JSON.stringify(definition), 'utf8').digest('hex')}`;
}

function workflowDigest(workflow) {
  return digestDefinition(workflowDefinition(workflow));
}

function workflowFile(config, id) {
  return path.join(resolveWorkflowDir(config), `${workflowId(id)}.json`);
}

function workflowDirsFor(config, options = {}) {
  const scope = String(options.scope || '').trim();
  const dirs = workflowDirs(config, options.cwd);
  return scope ? dirs.filter((item) => item.scope === scope) : dirs;
}

function saveWorkflow(config, raw) {
  if (!raw || typeof raw !== 'object') throw new Error('フローが不正です');
  if (READONLY_SCOPES[raw._scope]) {
    throw new Error(`${READONLY_SCOPES[raw._scope]}は読み取り専用です。別の id で自分用に保存してください`);
  }
  const current = raw && raw.id ? loadWorkflow(config, raw.id, { scope: 'user' }) : null;
  const clean = normalizeWorkflow({ ...raw, createdAt: (current && current.createdAt) || raw.createdAt });
  // ユーザー保存物は通常ライブラリとして扱う。同梱 internal 定義を複製した場合も、
  // 保存済み一覧から自分の定義まで隠れないようにする。
  clean.libraryVisibility = 'library';
  clean.updatedAt = new Date().toISOString();
  writeJsonAtomic(workflowFile(config, clean.id), clean);
  return { ...clean, _scope: 'user' };
}

function loadWorkflow(config, id, options = {}) {
  const dirs = workflowDirsFor(config, options);
  for (const item of dirs) {
    try {
      const clean = normalizeWorkflow(
        JSON.parse(fs.readFileSync(path.join(item.dir, `${workflowId(id)}.json`), 'utf8')),
        options,
      );
      return { ...clean, _scope: item.scope, ...(item.repository ? { _repository: item.repository } : {}) };
    } catch (err) {
      if (err && err.code === 'ENOENT') continue;
      throw err;
    }
  }
  return null;
}

function listWorkflows(config, options = {}) {
  const found = [];
  const ids = new Set();
  const purpose = normalizeExpectedPurpose(options.purpose);
  const includeInternal = options.includeInternal === true;
  const includeShadowed = options.includeShadowed === true;
  for (const item of workflowDirsFor(config, options)) {
    if (!fs.existsSync(item.dir)) continue;
    for (const entry of fs.readdirSync(item.dir, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
      try {
        const clean = normalizeWorkflow(JSON.parse(fs.readFileSync(path.join(item.dir, entry.name), 'utf8')));
        if (purpose && clean.purpose !== purpose) continue;
        if (!includeInternal && clean.libraryVisibility === 'internal') continue;
        if (!includeShadowed && ids.has(clean.id)) continue;
        ids.add(clean.id); // 同じ id は、先に探索するリポジトリ定義を優先する。
        found.push({
          ...clean,
          digest: workflowDigest(clean),
          _scope: item.scope,
          ...(item.repository ? { _repository: item.repository } : {}),
        });
      } catch { /* 壊れた1ファイルでカタログ全体を壊さない */ }
    }
  }
  return found.sort((a, b) => a.name.localeCompare(b.name));
}

function deleteWorkflow(config, id, options = {}) {
  if (READONLY_SCOPES[options.scope]) {
    throw new Error(`${READONLY_SCOPES[options.scope]}は読み取り専用です。dashboard からは削除できません`);
  }
  const file = workflowFile(config, id);
  if (!fs.existsSync(file)) return false;
  const trash = path.join(path.dirname(file), '.trash');
  fs.mkdirSync(trash, { recursive: true });
  fs.renameSync(file, path.join(trash, `${workflowId(id)}-${Date.now()}.json`));
  return true;
}

function patternCatalog(config) {
  const cmd = String(cfgOf(config).agentFlowCommand || '').trim() || 'agent-flow';
  const result = exec.shInWsl(`${cmd} patterns --json`, 10000, cfgOf(config).distro || '');
  if (result.status !== 0) return [];
  try {
    const rows = JSON.parse(result.stdout);
    return Array.isArray(rows) ? rows.filter((r) => r && r.id && r.label) : [];
  } catch {
    return [];
  }
}

// 実行方針の現在地（plan 生成の入力）。profiles / control が無い・壊れている端末でも
// plan 生成自体は止めない（既定値で決める）。
function flowStrategy(config) {
  let profile;
  try {
    profile = profiles.load(config);
  } catch {
    profile = null;
  }
  let controlTier;
  try {
    const ctl = control.loadControl(control.resolveControlDir(config));
    controlTier = String(((ctl.workloads || {}).flow || {}).tier || '');
  } catch {
    controlTier = '';
  }
  const executionPolicy = (profile && profile.executionPolicy) || {};
  const policy = (profile && profile.policy) || {};
  return {
    mode: executionPolicy.mode || 'auto',
    custom: executionPolicy.custom || {},
    policyTiers: [...new Set([
      policy.no_cap_tier,
      ...(Array.isArray(policy.steps) ? policy.steps.map((step) => step.tier) : []),
    ].filter(Boolean))],
    controlTier,
  };
}

function tierNames(tiers) {
  return tiers.map((tier) => flowTiers.TIER_LABELS[tier] || tier).join('・');
}

function normalizeExecutionOverrides(config, raw) {
  if (!raw || typeof raw !== 'object' || Number(raw.version) !== 1) return null;
  const mode = ['saving', 'quality', 'cost', 'custom'].includes(String(raw.mode || ''))
    ? String(raw.mode) : '';
  const inventory = agents.list(config);
  const available = new Set([
    ...inventory.builtins,
    ...inventory.dropins.filter((item) => !item.shadowed && !(item.errors || []).length)
      .map((item) => item.name),
  ]);
  const catalog = flowTiers.catalog();
  const out = { version: 1, ...(mode ? { mode } : {}), roles: {}, kinds: {} };
  const add = (group, key, value, allowed) => {
    if (!value || typeof value !== 'object') return;
    const tier = String(value.tier || '').trim();
    if (tier && tier !== 'auto' && flowTiers.isKnownTier(tier) && !allowed.includes(tier)) {
      throw new Error(`${group === 'kinds' ? '機能' : '役割'}「${key}」は実行レベル「${
        flowTiers.TIER_LABELS[tier] || tier}」に任せられません`);
    }
    const candidate = tier && tier !== 'auto' ? profiles.resolveTier(config, tier) : null;
    if (tier && tier !== 'auto' && (!candidate || !candidate.agent_cli)) {
      throw new Error(`tier「${tier}」で実行できるエージェントがありません`);
    }
    const explicitAgent = String(value.agent_cli || '').trim();
    const agentCli = explicitAgent || String((candidate && candidate.agent_cli) || '').trim();
    if (agentCli && !available.has(agentCli)) throw new Error(`エージェント「${agentCli}」は利用できません`);
    const model = String(value.model || (!explicitAgent || explicitAgent === (candidate && candidate.agent_cli)
      ? (candidate && candidate.model) : '') || '').trim();
    if (!tier && !agentCli && !model) return;
    out[group][key] = {
      ...(tier && tier !== 'auto' ? { tier } : {}),
      ...(agentCli ? { agent_cli: agentCli } : {}),
      ...(model ? { model } : {}),
      source: group === 'kinds' ? 'run-kind' : 'run-role',
      pinned: true,
    };
  };
  for (const role of EXECUTION_ROLES) {
    const spec = catalog.roles[role] || { tiers: flowTiers.TIER_ORDER };
    add('roles', role, raw.roles && raw.roles[role], spec.tiers);
  }
  for (const kind of NODE_KINDS.filter((item) => item !== 'human')) {
    const spec = catalog.kinds[kind] || { tiers: flowTiers.TIER_ORDER };
    add('kinds', kind, raw.kinds && raw.kinds[kind], spec.tiers);
  }
  return mode || Object.keys(out.roles).length || Object.keys(out.kinds).length ? out : null;
}

// ノード単位の人による割り当て（タスク追加ダイアログ・実行前フロー表示の選択結果）。
// 形: { <nodeId>: { tier, agent_cli, model } }。tier はそのノードの実行可能レベルの中から、
// 候補は profiles.json でその tier に宣言された組み合わせだけを受け付ける（自由入力を
// plan へ運ばない——資格の無い組み合わせが実行時に黙って走る経路を作らないため）。
// フロー定義の更新でノード id が消えた割り当ては黙って捨てる（自動割り当てで実行できる）。
function normalizeNodeAssignments(config, clean, raw) {
  if (!raw || typeof raw !== 'object') return {};
  const byId = new Map(clean.nodes.map((n) => [n.id, n]));
  const profile = profiles.load(config);
  const out = {};
  for (const [nodeId, value] of Object.entries(raw)) {
    const node = byId.get(String(nodeId));
    if (!node || node.kind === 'human' || !value || typeof value !== 'object') continue;
    const tier = String(value.tier || '').trim();
    if (!tier) continue;
    const allowed = flowTiers.allowedTiers(node.kind, node);
    if (flowTiers.isKnownTier(tier) && !allowed.includes(tier)) {
      throw new Error(`ノード「${node.id}」（${node.kind}）は実行レベル「${
        flowTiers.TIER_LABELS[tier] || tier}」に任せられません（選べる実行レベル: ${tierNames(allowed)}）`);
    }
    const spec = (profile.tiers || {})[tier];
    if (!spec) throw new Error(`実行レベル「${tier}」は定義されていません`);
    const candidate = (spec.candidates || []).find((item) => item
      && String(item.agent_cli || '') === String(value.agent_cli || '')
      && String(item.model || '') === String(value.model || ''));
    if (!candidate || !candidate.agent_cli) {
      throw new Error(`ノード「${node.id}」に選択したエージェントとモデルは実行レベル「${
        flowTiers.TIER_LABELS[tier] || spec.label || tier}」の候補にありません`);
    }
    out[node.id] = { tier, agent_cli: String(candidate.agent_cli), model: String(candidate.model || '') };
  }
  return out;
}

const DESIGN_NODE_CONTRACT = '設計runとして実行します。リポジトリは読み取り専用です。ファイルの変更、commit、pushは禁止です。';
const DESIGN_OUTPUT_CONTRACT = [
  '最終成果は設計書の全文です。次の4節を必ず含めてください。',
  '## 目的',
  '## 変更対象',
  '## 受入基準',
  '## 検証方法',
  '未決事項があれば「## 質問」節を作り、推奨する答えとその理由を添えてください。',
].join('\n');

function planFromWorkflow(config, workflow, options = {}) {
  const clean = normalizeWorkflow(workflow, { purpose: options.purpose });
  const purpose = options.purpose || clean.purpose;
  const assignments = normalizeNodeAssignments(config, clean, options.nodeAssignments);
  const candidates = new Map();
  const strategy = flowStrategy(config);
  const resolveCandidate = (tier) => {
    if (!candidates.has(tier)) candidates.set(tier, profiles.resolveTier(config, tier));
    return candidates.get(tier);
  };
  const nodes = clean.nodes.map((n) => {
    if (n.kind === 'human') {
      return { id: n.id, goal: n.goal, kind: n.kind, deps: n.deps, interaction: n.interaction };
    }
    const baseGoal = n.method
      ? `${n.goal}\n\n実行手法「${n.method.description || n.method.id}」:\n${n.method.text}`
      : n.goal;
    const goal = purpose === 'design'
      ? `${baseGoal}\n\n${DESIGN_NODE_CONTRACT}`
      : baseGoal;
    const terminalGoal = purpose === 'design' && clean.exit.includes(n.id)
      ? `${goal}\n\n${DESIGN_OUTPUT_CONTRACT}`
      : goal;
    const assigned = assignments[n.id];
    if (assigned) {
      // 人の選択は自動割り当てより優先し、固定 tier ノードと同じ扱い（降格しない）で運ぶ。
      return {
        id: n.id,
        goal: terminalGoal,
        kind: n.kind,
        deps: n.deps,
        tier: assigned.tier,
        agent: { agent_cli: assigned.agent_cli, ...(assigned.model ? { model: assigned.model } : {}) },
        selection_reason: 'user-node-assignment',
      };
    }
    const allowed = flowTiers.allowedTiers(n.kind, n);
    let tier = n.tier;
    let pinReason = '';
    if (tier === 'auto') {
      // 複数の実行レベルで実行可能な振る舞いは、実行方針（戦略）に応じて決める。
      // 方針が選びうる段がすべて実行可能なら plan へ tier を書かず実行時の追従に任せ、
      // 不適格な段へ落ちうる場合だけ、今の段を実行可能範囲へ丸めて固定する。
      const decision = flowTiers.decideAutoTier({
        kind: n.kind, node: n, mode: strategy.mode, custom: strategy.custom,
        policyTiers: strategy.policyTiers, controlTier: strategy.controlTier,
      });
      if (decision.inherit) {
        return { id: n.id, goal: terminalGoal, kind: n.kind, deps: n.deps };
      }
      tier = decision.tier;
      pinReason = decision.reason || '';
    } else if (flowTiers.isKnownTier(tier) && !allowed.includes(tier)) {
      // 固定 tier の適格性。オプション（route / retry）で下限が上がる場合も同じ口で弾く。
      throw new Error(`ノード「${n.id}」（${n.kind}）は実行レベル「${
        flowTiers.TIER_LABELS[tier] || tier}」に任せられません（選べる実行レベル: ${tierNames(allowed)}）`);
    }
    const candidate = resolveCandidate(tier);
    if (!candidate || !candidate.agent_cli) {
      throw new Error(`tier「${tier}」で実行できるエージェントがありません`);
    }
    return {
      id: n.id,
      goal: terminalGoal,
      kind: n.kind,
      deps: n.deps,
      tier,
      agent: { agent_cli: candidate.agent_cli, ...(candidate.model ? { model: candidate.model } : {}) },
      ...(pinReason ? { selection_reason: pinReason } : {}),
    };
  });
  return {
    name: clean.name,
    nodes,
    ...(clean.nodes.some((node) => node.continuation) ? { evaluate: true } : {}),
  };
}

function snapshotSelection(config, selection, options = {}) {
  const selected = selection && typeof selection === 'object' ? selection : { type: 'auto' };
  const expectedPurpose = normalizeExpectedPurpose(
    options.purpose === undefined ? selected.purpose : options.purpose,
  );
  const type = String(selected.type || 'auto');
  if (type === 'auto') {
    if (expectedPurpose === 'design') throw new Error('設計フローにはカスタムの design フローが必要です');
    return { version: 1, type: 'auto' };
  }
  if (type === 'pattern') {
    if (expectedPurpose === 'design') throw new Error('設計フローには標準の実装パターンを使用できません');
    const pattern = String(selected.id || selected.pattern || '').trim();
    if (!pattern) throw new Error('標準フローを選択してください');
    return { version: 1, type: 'pattern', pattern };
  }
  if (type === 'custom') {
    const workflow = loadWorkflow(config, selected.id, {
      cwd: options.cwd, scope: selected.scope, purpose: expectedPurpose,
    });
    if (!workflow) throw new Error('カスタムフローが見つかりません');
    return {
      version: 1,
      type: 'custom',
      id: workflow.id,
      purpose: workflow.purpose,
      digest: workflowDigest(workflow),
      ...planFromWorkflow(config, workflow, {
        nodeAssignments: selected.nodeAssignments,
        purpose: expectedPurpose || workflow.purpose,
      }),
    };
  }
  throw new Error('フロー選択が不正です');
}

// --- 割り当てプレビュー（自動割り当ての可視化と選択肢） -----------------------
// 実行前に「自動ならどの実行レベル・どの候補になるか」と「人が選べる候補一覧」を
// UI へ渡すための読み取り専用ビュー。plan 生成（planFromWorkflow）と同じ材料
// （flowStrategy / flowTiers / profiles）から導出し、決定ロジックを複製しない。

function autoAssignmentFor(config, strategy, allowed) {
  const policy = (strategy.policyTiers || []).map(String).filter(Boolean);
  const inherit = policy.length > 0
    && policy.every((tier) => !flowTiers.isKnownTier(tier) || allowed.includes(tier));
  const base = flowTiers.isKnownTier(strategy.controlTier)
    ? String(strategy.controlTier)
    : flowTiers.modeDefaultTier(strategy.mode, strategy.custom);
  const tier = flowTiers.clampTier(allowed, base, strategy.mode);
  const candidate = tier ? profiles.resolveTier(config, tier) : null;
  return { inherit, tier, candidate };
}

// profiles.json の宣言から、標準語彙の各実行レベルで選べる候補一覧を組む。
function tierCandidateCatalog(config) {
  const profile = profiles.load(config);
  const out = {};
  for (const tier of flowTiers.TIER_ORDER) {
    const spec = (profile.tiers || {})[tier];
    const candidates = spec && Array.isArray(spec.candidates)
      ? spec.candidates
        .filter((candidate) => candidate && candidate.agent_cli)
        .map((candidate) => ({
          agent_cli: String(candidate.agent_cli),
          model: String(candidate.model || ''),
        }))
      : [];
    if (candidates.length) {
      out[tier] = { label: flowTiers.TIER_LABELS[tier] || (spec && spec.label) || tier, candidates };
    }
  }
  return out;
}

// フロー（設計フロー等）のノードごとの自動割り当てと選択可能な候補。
function flowAssignmentPreview(config, { id, cwd, scope, workflow, purpose } = {}) {
  // 設計 preview は IPC が解決した workflow をそのまま使う。ここで再読込すると、
  // 解決直後の元ファイル編集・削除で preview だけ別定義になるため、読み取り用の
  // workflow を渡せるようにする（workflow は Renderer 入力ではなく main 内の値）。
  const resolved = workflow || loadWorkflow(config, id, { cwd, scope });
  if (!resolved) throw new Error(`フローが見つかりません: ${id}`);
  const clean = normalizeWorkflow(resolved, { purpose });
  const strategy = flowStrategy(config);
  const nodes = clean.nodes.map((n) => {
    if (n.kind === 'human') {
      return { id: n.id, label: n.label, kind: n.kind, human: true };
    }
    const allowed = flowTiers.allowedTiers(n.kind, n);
    if (n.tier && n.tier !== 'auto') {
      const candidate = profiles.resolveTier(config, n.tier);
      return { id: n.id, label: n.label, kind: n.kind, allowed, auto: { inherit: false, tier: n.tier, candidate } };
    }
    return { id: n.id, label: n.label, kind: n.kind, allowed, auto: autoAssignmentFor(config, strategy, allowed) };
  });
  return {
    id: clean.id,
    name: clean.name,
    order: flowTiers.TIER_ORDER.slice(),
    labels: { ...flowTiers.TIER_LABELS },
    tierCandidates: tierCandidateCatalog(config),
    nodes,
  };
}

// 実装フロー（自動 plan）の役割・機能ごとの自動割り当てと選択可能な候補。
// 実行前のフロー表示で「今実行するとこうなる」を見せ、execution_overrides の入力に使う。
function executionAssignmentPreview(config) {
  const strategy = flowStrategy(config);
  const catalog = flowTiers.catalog();
  const entry = (allowed) => ({ allowed, auto: autoAssignmentFor(config, strategy, allowed) });
  const roles = {};
  for (const role of EXECUTION_ROLES) {
    roles[role] = entry((catalog.roles[role] || { tiers: flowTiers.TIER_ORDER }).tiers);
  }
  const kinds = {};
  for (const kind of NODE_KINDS.filter((item) => item !== 'human')) {
    kinds[kind] = entry((catalog.kinds[kind] || { tiers: flowTiers.TIER_ORDER }).tiers);
  }
  return {
    order: flowTiers.TIER_ORDER.slice(),
    labels: { ...flowTiers.TIER_LABELS },
    tierCandidates: tierCandidateCatalog(config),
    roles,
    kinds,
  };
}

// --- 一貫性ゲート（codd-gate の差分ゲートを run 内の統一 verify に載せる） -----
// 検証計画（verification_plan）の固定コマンドとして codd-gate を運ぶ。fail はエンジンの
// verify-fix ループが同じ run 内で自己修復し、codd-gate 不在の端末は exit 127 =
// inconclusive（黙って PASS しない）。digest の計算は agentcore.verifycontract の
// 1 実装が正典なので、ここでは組み立てを `agent-flow verify-plan` へ委譲して
// 返った JSON をそのまま inbox 要求に運ぶだけ（複製実装しない）。

const COHERENCE_COMMAND = 'codd-gate verify --base "$AGENT_BASE_REV"';

function buildVerificationPlan(config, { runId, workspace }) {
  const c = cfgOf(config);
  const cmd = String(c.agentFlowCommand || '').trim() || 'agent-flow';
  const q = exec.shellQuote;
  const line = `${cmd} verify-plan --task-id ${q(runId)} --workspace ${q(workspace.url)}`
    + ` --command ${q(COHERENCE_COMMAND)}`;
  const result = exec.shInWsl(line, 10000, c.distro || '');
  let plan = null;
  try {
    plan = JSON.parse(String(result.stdout || ''));
  } catch { /* 下の fail-close に落とす */ }
  if (result.status !== 0 || !plan || !plan.digest) {
    // フェイルクローズ: 頼まれたゲートを黙って外したまま起動しない
    throw new Error(`一貫性ゲートの検証計画を組み立てられませんでした: ${
      String(result.stderr || result.stdout || '').trim().slice(0, 300)}`);
  }
  return plan;
}

function gitWorkspace(config, cwd) {
  const entered = String(cwd || '').trim();
  if (!entered) throw new Error('Gitリポジトリを選択してください');
  const linuxPath = exec.toWslCwd(entered);
  const q = exec.shellQuote;
  const line = `root=$(git -C ${q(linuxPath)} rev-parse --show-toplevel 2>/dev/null) || exit 2; `
    + 'base=$(git -C "$root" symbolic-ref --short -q HEAD 2>/dev/null '
    + '|| git -C "$root" rev-parse HEAD 2>/dev/null) || exit 3; '
    + 'remote=$(git -C "$root" remote get-url origin 2>/dev/null) || exit 4; '
    + 'printf \'%s\\n%s\\n%s\\n\' "$root" "$base" "$remote"';
  const result = exec.shInWsl(line, 10000, cfgOf(config).distro || '');
  const [root, base, remote] = String(result.stdout || '').split(/\r?\n/).filter(Boolean);
  if (result.status !== 0 || !root || !base || !remote) {
    throw new Error('共有リモート origin がある Git 管理フォルダを選択してください');
  }
  return { url: remote, local: root, base, path: '', desc: 'workflow' };
}

// --- プリセット（保存済みフロー定義） ---------------------------------------
// ビルダーの成果物。実行時に投入契約（plan）へ変換されるだけの宣言データで、
// 検証の正典はエンジン側（agent_flow plan_strategy_user）。ここでは保存物が
// 壊れていないかの軽い整形だけを行い、検証ロジックを複製しない（C7）。

function normalizePreset(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('プリセットが不正です');
  const name = String(raw.name || '').trim();
  if (!name) throw new Error('プリセット名は必須です');
  const nodes = [];
  const seen = new Set();
  for (const n of Array.isArray(raw.nodes) ? raw.nodes : []) {
    const id = String((n && n.id) || '').trim();
    const goal = String((n && n.goal) || '').trim();
    if (!id || !goal) throw new Error('ノードには id と goal が必要です');
    if (seen.has(id)) throw new Error(`ノード id が重複しています: ${id}`);
    seen.add(id);
    nodes.push({
      id,
      goal,
      kind: String((n && n.kind) || 'work').trim() || 'work',
      deps: (Array.isArray(n.deps) ? n.deps : []).map((d) => String(d).trim()).filter(Boolean),
      agentCli: String((n && n.agentCli) || '').trim(),
      model: String((n && n.model) || '').trim(),
    });
  }
  return {
    id: String(raw.id || '').trim() || `preset-${Date.now()}`,
    name,
    description: String(raw.description || '').trim(),
    evaluate: raw.evaluate === true,
    nodes,
    methods: (Array.isArray(raw.methods) ? raw.methods : [])
      .map((m) => String(m).trim()).filter(Boolean),
    agentCli: String(raw.agentCli || '').trim(),
    model: String(raw.model || '').trim(),
    planner: String(raw.planner || '').trim(),
    updatedAt: new Date().toISOString(),
  };
}

// プリセット → 投入契約の plan（submit_request / plan_strategy_user の語彙）。
// ノードが無いプリセット（手法・エンジン設定だけの使い回し）は plan 無し＝planner に任せる。
function planFromPreset(preset) {
  if (!preset || !preset.nodes || !preset.nodes.length) return null;
  const nodes = preset.nodes.map((n) => {
    const node = { id: n.id, goal: n.goal, deps: n.deps || [], kind: n.kind || 'work' };
    if (n.agentCli) {
      node.agent = { agent_cli: n.agentCli, ...(n.model ? { model: n.model } : {}) };
    }
    return node;
  });
  const plan = { name: preset.name, nodes };
  if (preset.evaluate) plan.evaluate = true;
  return plan;
}

// --- 手法（F17）の run 専用スナップショット ----------------------------------
// 選んだ手法だけを run 専用の tuning.json へ複製し、AGENT_TUNING_DIR で agent-flow に
// 読ませる（methods.py の唯一の per-run 強制口）。端末全体の tuning.json はこの run では
// **読まれない**（置換であって合成ではない）——どの手法が効いたかを run 単位で決定的に
// するための意図した挙動で、UI にもそう表示する。

function availableMethods(config, options = {}) {
  const seen = new Map();
  for (const m of tuning.catalog(config)) {
    seen.set(String(m.id), { ...m, _from: 'catalog' });
  }
  const state = tuning.load(config);
  for (const m of Array.isArray(state.methods) ? state.methods : []) {
    if (m && m.id) seen.set(String(m.id), { ...m, _from: 'tuning' });
  }
  const registered = registeredRepositoryRoot(config, options.cwd);
  const repoDir = registered ? path.join(registered, '.agents', 'methods') : '';
  if (repoDir && fs.existsSync(repoDir)) {
    for (const entry of fs.readdirSync(repoDir, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
      try {
        const method = JSON.parse(fs.readFileSync(path.join(repoDir, entry.name), 'utf8'));
        if (!method || typeof method !== 'object' || !method.id) continue;
        const body = JSON.parse(JSON.stringify(method));
        body.source = `repository:.agents/methods/${entry.name}@${tuning.sourceHash(method)}`;
        seen.set(String(method.id), { ...body, _from: 'repository', _repository: registered });
      } catch { /* 壊れた1ファイルで手法一覧全体を壊さない */ }
    }
  }
  return [...seen.values()];
}

function methodsSnapshot(config, ids) {
  if (!ids || !ids.length) return null;
  const avail = new Map(availableMethods(config).map((m) => [String(m.id), m]));
  const out = [];
  for (const id of ids) {
    const m = avail.get(String(id));
    if (!m) throw new Error(`手法が見つかりません: ${id}`);
    const { _from, ...body } = m;
    const snap = JSON.parse(JSON.stringify(body));
    snap.enabled = true;
    if (_from === 'catalog') snap.source = `methods/${snap.id}@${tuning.sourceHash(body)}`;
    else if (!snap.source) snap.source = `custom/${snap.id}`;
    out.push(snap);
  }
  return out;
}

function runTuningDir(config, runId) {
  const root = String(cfgOf(config).tuningRoot || '').trim();
  return root ? path.join(root, runId) : agentHomeSubdir('flow', 'tuning', runId);
}

function writeRunTuning(config, runId, methods) {
  const dir = runTuningDir(config, runId);
  fs.mkdirSync(dir, { recursive: true });
  const data = {
    version: 1,
    revision: 1,
    enabled: true,
    methods,
    trials: [],
    profiles: { default: {}, 'external-facing': { injections: [] } },
    updated_at: new Date().toISOString(),
    updated_by: 'agent-dashboard adhoc-flow',
  };
  writeJsonAtomic(path.join(dir, 'tuning.json'), data);
  return dir;
}

// --- 投入と起動 ---------------------------------------------------------------

function newRunId() {
  const t = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}`
    + `-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`;
  return `adhoc-${stamp}-${Math.floor(1000 + Math.random() * 9000)}`;
}

// 起動シェル行の組み立て（テスト可能にするため純関数で切り出す）。
// nohup + & で切り離す——run は自己完結（heartbeat・park 監視持ち）なので、
// dashboard の生存に依存させない（C6）。ログは $HOME 基準で組む（WSL でも壊れない）。
function buildLaunchLine(config, { runId, busDir, tuningDir, agentCli, model, planner }) {
  const c = cfgOf(config);
  const q = exec.shellQuote;
  const cmd = String(c.agentFlowCommand || '').trim() || 'agent-flow';
  const guard = String(c.agentFlowCommand || '').trim()
    ? ''
    : 'command -v agent-flow >/dev/null 2>&1 || { echo agent-flow-not-found >&2; exit 127; }; ';
  const env = tuningDir ? `AGENT_TUNING_DIR=${q(exec.toWslCwd(tuningDir))} ` : '';
  const flags = [
    '--bus', q(exec.toWslCwd(busDir)), '--run-id', q(runId), 'run', '--from-inbox',
    ...(planner ? ['--planner', q(planner)] : []),
    ...(agentCli ? ['--agent-cli', q(agentCli)] : []),
    ...(model ? ['--model', q(model)] : []),
  ].join(' ');
  return `${guard}LOGDIR="$HOME/.agents/flow/logs"; mkdir -p "$LOGDIR"; `
    + `${env}nohup ${cmd} ${flags} >> "$LOGDIR/${runId}.log" 2>&1 & echo launched:$!`;
}

function buildForceCompleteLine(config, { busDir, runId, reason }) {
  const c = cfgOf(config);
  const q = exec.shellQuote;
  const cmd = String(c.agentFlowCommand || '').trim() || 'agent-flow';
  const guard = String(c.agentFlowCommand || '').trim()
    ? ''
    : 'command -v agent-flow >/dev/null 2>&1 || { echo agent-flow-not-found >&2; exit 127; }; ';
  return `${guard}${cmd} --bus ${q(exec.toWslCwd(busDir))} force-complete ${q(runId)} --reason ${q(reason)}`;
}

function forceComplete(config, { runId, reason } = {}) {
  const id = String(runId || '').trim();
  const why = String(reason || '').trim();
  if (!id || path.basename(id) !== id) throw new Error(`不正な run ID です: ${runId}`);
  if (!why) throw new Error('強制復旧の理由を入力してください');
  const line = buildForceCompleteLine(config, { busDir: resolveBusDir(config), runId: id, reason: why });
  const result = exec.shInWsl(line, 120000, cfgOf(config).distro || '');
  if (result.status !== 0) {
    throw new Error(String(result.stderr || result.stdout || 'force-complete に失敗しました').trim().slice(0, 500));
  }
  return { runId: id, message: String(result.stdout || '').trim() };
}

function submit(config, {
  title, request, preset, cwd, selection, purpose, executionOverrides, coherenceGate,
} = {}) {
  const req = String(request || '').trim();
  if (!req) throw new Error('要求テキストは必須です');
  const expectedPurpose = normalizeExpectedPurpose(purpose);
  const p = preset ? normalizePreset(preset) : null;
  if (expectedPurpose === 'design' && p) {
    throw new Error('設計runでは実装用プリセットを使用できません');
  }
  const selected = selection && typeof selection === 'object' ? selection : { type: 'auto' };
  const snapshot = snapshotSelection(config, selected, {
    cwd, ...(expectedPurpose ? { purpose: expectedPurpose } : {}),
  });
  const effectivePurpose = snapshot.purpose || expectedPurpose || 'implementation';
  if (effectivePurpose === 'design' && p) {
    throw new Error('設計runでは実装用プリセットを使用できません');
  }
  if (effectivePurpose === 'design' && coherenceGate) {
    throw new Error('設計runでは一貫性ゲートを使用できません');
  }
  const runId = newRunId();
  const busDir = resolveBusDir(config);
  fs.mkdirSync(path.join(busDir, 'inbox'), { recursive: true });

  // cwd は設計フローの参照解決には使えるが、設計runは必ず読み取り専用の
  // workspace=null 契約で投入する。gitWorkspace を呼ばないこと自体も重要で、
  // cwd指定だけで af/<run-id> 用のworkspaceを作成しない。
  const workspace = effectivePurpose === 'design' ? null : (cwd ? gitWorkspace(config, cwd) : null);
  if (coherenceGate && !workspace) {
    throw new Error('一貫性ゲートには Git リポジトリの選択が必要です（差分ゲートは書込先で判定します）');
  }
  const rec = {
    id: runId,
    ...(String(title || '').trim() ? { title: String(title).trim() } : {}),
    request: req,
    submitter: SUBMITTER,
    purpose: effectivePurpose,
    workspace,
    references: [],
    submitted_at: new Date().toISOString(),
  };
  if (coherenceGate) rec.verification_plan = buildVerificationPlan(config, { runId, workspace });
  let plan = p ? planFromPreset(p) : null;
  if (snapshot.type === 'custom') {
    plan = { name: snapshot.name, nodes: snapshot.nodes, ...(snapshot.evaluate ? { evaluate: true } : {}) };
  }
  if (snapshot.type === 'pattern') rec.pattern = snapshot.pattern;
  if (plan) rec.plan = plan;
  const execution = normalizeExecutionOverrides(config, executionOverrides);
  if (execution) rec.execution_overrides = execution;
  writeJsonAtomic(path.join(busDir, 'inbox', `${runId}.json`), rec);

  const methods = methodsSnapshot(config, p ? p.methods : snapshot.methods);
  const tuningDir = methods && methods.length ? writeRunTuning(config, runId, methods) : null;

  const line = buildLaunchLine(config, {
    runId,
    busDir,
    tuningDir,
    agentCli: p ? p.agentCli : '',
    model: p ? p.model : '',
    planner: p ? p.planner : '',
  });
  const r = exec.shInWsl(line, 20000, cfgOf(config).distro || '');
  if (r.status !== 0 || /agent-flow-not-found/.test(String(r.stderr || ''))) {
    throw new Error(`agent-flow の起動に失敗しました: ${String(r.stderr || r.stdout || '').trim().slice(0, 400)}`);
  }
  return {
    runId,
    busDir,
    tuningDir,
    plan: !!plan,
    methods: methods ? methods.map((m) => m.id) : [],
  };
}

// 再投入: 旧 run の inbox 記録（自分が書いた契約）を新しい id で写す。plan も引き継ぐ。
// 旧 run が消えていても inbox 記録が残っていれば再投入できる。
function resubmit(config, runId) {
  const busDir = resolveBusDir(config);
  const old = readInbox(busDir, runId);
  if (!old) throw new Error(`inbox 記録が見つかりません: ${runId}`);
  const purpose = normalizeExpectedPurpose(old.purpose || 'implementation') || 'implementation';
  if (purpose === 'design' && old.pattern) {
    throw new Error('設計runへ実装用パターンを再投入できません');
  }
  const next = newRunId();
  const rec = {
    ...old,
    id: next,
    purpose,
    // 古いinboxにworkspaceが残っていても、再投入で設計runの契約を復元する。
    workspace: purpose === 'design' ? null : (old.workspace || null),
    submitted_at: new Date().toISOString(),
  };
  if (purpose === 'design') delete rec.verification_plan;
  writeJsonAtomic(path.join(busDir, 'inbox', `${next}.json`), rec);
  // 旧 run の手法スナップショットも新 run へ写す（同条件の再実行）
  let tuningDir = null;
  const oldTuning = path.join(runTuningDir(config, runId), 'tuning.json');
  if (fs.existsSync(oldTuning)) {
    const data = JSON.parse(fs.readFileSync(oldTuning, 'utf8'));
    tuningDir = writeRunTuning(config, next, Array.isArray(data.methods) ? data.methods : []);
  }
  const line = buildLaunchLine(config, { runId: next, busDir, tuningDir });
  const r = exec.shInWsl(line, 20000, cfgOf(config).distro || '');
  if (r.status !== 0) {
    throw new Error(`agent-flow の起動に失敗しました: ${String(r.stderr || r.stdout || '').trim().slice(0, 400)}`);
  }
  return { runId: next, busDir };
}

function readInbox(busDir, runId) {
  try {
    return JSON.parse(fs.readFileSync(path.join(busDir, 'inbox', `${runId}.json`), 'utf8'));
  } catch {
    return null;
  }
}

function sweepExpiredRuns(config, nowMs = Date.now()) {
  const days = Math.max(1, Number(cfgOf(config).retentionDays) || 30);
  const cutoff = nowMs - days * 86400000;
  const busDir = resolveBusDir(config);
  const runsDir = path.join(busDir, 'runs');
  const removed = [];
  for (const runId of fs.existsSync(runsDir) ? fs.readdirSync(runsDir) : []) {
    if (path.basename(runId) !== runId) continue;
    const runDir = path.join(runsDir, runId);
    const meta = (() => {
      try { return JSON.parse(fs.readFileSync(path.join(runDir, 'meta.json'), 'utf8')); }
      catch { return null; }
    })();
    if (!meta || !['done', 'failed', 'cancelled', 'canceled'].includes(String(meta.status))) continue;
    const updated = Date.parse(meta.updated_at || meta.created_at || '');
    if (!Number.isFinite(updated) || updated >= cutoff) continue;
    const current = flow.readRun(runDir);
    if ((current.interactions || []).some((item) => !item.resolution && !item.expired)) continue;
    fs.rmSync(runDir, { recursive: true });
    fs.rmSync(path.join(busDir, 'inbox', `${runId}.json`), { force: true });
    fs.rmSync(runTuningDir(config, runId), { recursive: true, force: true });
    removed.push(runId);
  }
  return removed.sort();
}

// --- 昇格（S22 / M1 後半） ----------------------------------------------------
// run の成果を agent-project の正式なタスクへ載せ替える。書くのは既存の inbox 投函契約
// （actions.enqueueToInbox）だけで、昇格したタスクは通常の受入基準と verify を通る。
// 昇格しない限りアドホックの成果は done にならない。

function promote(config, { projectDir, spec } = {}) {
  const dir = String(projectDir || '').trim();
  if (!dir) throw new Error('昇格先プロジェクトを選んでください');
  const engine = require('../../agent-project/main/engine');
  const roots = engine.projectRoots(config).map((r) => path.resolve(String(r)));
  if (!roots.includes(path.resolve(dir))) {
    // C1: 任意パスへの投函を許さない。実行エンジンが担当しているプロジェクトだけが宛先。
    throw new Error(`実行エンジンが担当しているプロジェクトではありません: ${dir}`);
  }
  const actions = require('../../agent-project/main/actions');
  return actions.enqueueToInbox(dir, spec || {});
}

function listProjects(config) {
  const engine = require('../../agent-project/main/engine');
  return engine.projectRoots(config).map((dir) => ({ dir, name: path.basename(dir) }));
}

module.exports = {
  SUBMITTER,
  NODE_KINDS,
  COHERENCE_COMMAND,
  buildVerificationPlan,
  resolveBusDir,
  resolveWorkflowDir,
  resolveBuiltinWorkflowDir,
  repositoryRoot,
  repositoryWorkflowDir,
  repositoryMethodsDir,
  workflowDefinition,
  definitionSnapshot: workflowDefinition,
  digestDefinition,
  workflowDigest,
  digestWorkflow: workflowDigest,
  registeredRepositoryRoot,
  normalizeWorkflow,
  saveWorkflow,
  loadWorkflow,
  listWorkflows,
  deleteWorkflow,
  patternCatalog,
  normalizeExecutionOverrides,
  normalizeNodeAssignments,
  planFromWorkflow,
  flowAssignmentPreview,
  executionAssignmentPreview,
  snapshotSelection,
  gitWorkspace,
  normalizePreset,
  planFromPreset,
  availableMethods,
  methodsSnapshot,
  writeRunTuning,
  runTuningDir,
  buildLaunchLine,
  buildForceCompleteLine,
  forceComplete,
  submit,
  resubmit,
  readInbox,
  sweepExpiredRuns,
  promote,
  listProjects,
  // 読み取りはバスパーサ（agent-project/main/flow.js）をそのまま使う
  flow,
};
