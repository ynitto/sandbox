'use strict';

// agent-flow との境界。renderer から bus のパスを受け取らず、登録済み root と id だけで
// 定義の投入・進捗の合成・人の回答を行う。

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flowModel = require('./flow-model');
const flowStore = require('./flow-store');
const flowSettings = require('./flow-settings');
const templateParameters = require('./template-parameters');

const TERMINAL = new Set(['done', 'failed', 'cancelled', 'canceled']);
const PHASES = new Set(['planning', 'executing', 'evaluating', 'verifying', 'finalizing']);
const NO_LEASE_GRACE_SECONDS = 600;
let patternCache = null;

function flowError(code, message, extra = {}) {
  return flowStore.flowError(code, message, extra);
}

function busDir() {
  return process.env.AGENT_APP_FLOW_BUS || path.join(os.homedir(), '.agents', 'flow', 'bus');
}

function logDir() {
  return process.env.AGENT_APP_FLOW_LOGS || path.join(path.dirname(busDir()), 'logs');
}

function safeList(dir) {
  try { return fs.readdirSync(dir); } catch { return []; }
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

function validRunId(runId) {
  const id = String(runId || '');
  if (!id || id !== path.basename(id) || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$/.test(id)) {
    throw flowError('run-not-found', '実行が見つかりません');
  }
  return id;
}

function isoSeconds(date = new Date()) {
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function runIdNow(date = new Date()) {
  const stamp = date.toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
  return `app-${stamp}-${crypto.randomBytes(2).toString('hex')}`;
}

function firstLine(result) {
  return String((result && (result.stderr || result.stdout || result.error)) || '').trim().split(/\r?\n/).find(Boolean) || '';
}

async function patterns(capture, cwd = '') {
  if (patternCache) return patternCache;
  const result = await capture('agent-flow', ['patterns', '--json'], { cwd, timeoutMs: 10000 });
  if (!result || !result.ok) {
    patternCache = { ok: false, patterns: [], summary: `起動できません: ${firstLine(result) || 'agent-flow'}` };
    return patternCache;
  }
  try {
    const rows = JSON.parse(String(result.stdout || '[]'));
    patternCache = { ok: Array.isArray(rows), patterns: Array.isArray(rows) ? rows : [], summary: '利用可能' };
  } catch {
    patternCache = { ok: false, patterns: [], summary: '標準パターンの一覧を読み取れません' };
  }
  return patternCache;
}

async function catalog(capture) {
  const found = await patterns(capture);
  return { kinds: flowModel.KIND_INFOS, patterns: found.patterns, limits: { maxNodes: flowModel.MAX_NODES, idPattern: flowModel.ID_RE.source } };
}

async function gitValue(capture, root, args) {
  const result = await capture('git', ['-C', root, ...args], { cwd: root, timeoutMs: 10000 });
  return result && result.ok ? String(result.stdout || '').trim() : '';
}

async function context({ root, capture, agentDefinitions, defaults = {} }) {
  const [agentsResult, tool, top, branchName, origin] = await Promise.all([
    Promise.resolve().then(() => agentDefinitions({ cwd: root, capture })).catch(() => []),
    patterns(capture, root),
    gitValue(capture, root, ['rev-parse', '--show-toplevel']),
    gitValue(capture, root, ['rev-parse', '--abbrev-ref', 'HEAD']),
    gitValue(capture, root, ['remote', 'get-url', 'origin']),
  ]);
  let branch = branchName;
  if (branch === 'HEAD') branch = await gitValue(capture, root, ['rev-parse', '--short', 'HEAD']);
  const workspaceOk = !!top && !!branch && !!origin;
  const reason = !top ? 'Git リポジトリではありません'
    : !origin ? '成果の公開先（origin）がありません'
      : !branch ? '現在のブランチを確認できません' : '';
  return {
    root,
    agents: Array.isArray(agentsResult) ? agentsResult : [],
    defaults: { agent: String(defaults.agent || ''), model: String(defaults.model || '') },
    workspace: { ok: workspaceOk, branch, origin, reason },
    tools: {
      agentFlow: {
        id: 'agent-flow', label: '複数AIワークフロー（agent-flow）', ok: !!tool.ok,
        summary: tool.ok ? `利用可能（標準パターン ${tool.patterns.length} 件）` : tool.summary,
        hint: tool.ok ? '' : 'tools/agent-flow/install.sh を実行し、agent-flow を PATH に通してください。',
      },
    },
    capabilities: { openDelivery: false },
    bus: busDir(),
  };
}

const SIZES = new Set(flowSettings.FIELDS.size.values);

// 画面で調整した設定が ~/.agents/agent-flow.yaml のとき、agent-flow にその 1 枚を名指しする。
// Windows から WSL の agent-flow を起こす構成では、両者のホームが違って見つけられないため。
// リポジトリ側に設定があるときは agent-flow が cwd から同じ 1 枚を見つけるので渡さない。
function configArgs(root, hostPath) {
  try {
    const found = flowSettings.locate(root);
    return found.exists && found.home ? ['--config', hostPath(found.file)] : [];
  } catch { return []; }
}

function validateRunParameters(workflow, request, raw) {
  const result = flowModel.preview(workflow, request, raw);
  if (result.ok) return result;
  const parameterIssues = result.issues.filter((item) => item.code === 'goal-has-unfilled-parameter');
  if (parameterIssues.length) {
    throw flowError('parameters-invalid', '実行時の入力項目を確認してください', { detail: parameterIssues.map((item) => item.message).join('、'), issues: parameterIssues });
  }
  throw flowError('flow-invalid', 'ワークフローの内容を直してから実行してください', { issues: result.issues });
}

async function start(payload, deps) {
  const request = String(payload.request || '').trim();
  if (!request) throw flowError('request-required', '依頼内容を入力してください');
  const source = payload.source && typeof payload.source === 'object' ? payload.source : { type: 'auto' };
  let workflow = null;
  if (source.type === 'workflow') workflow = flowStore.read(deps.root, source.id).workflow;
  else if (source.type === 'draft') workflow = source.workflow;
  else if (!['pattern', 'auto'].includes(source.type)) throw flowError('flow-invalid', '実行方法が不正です');
  let checked = null;
  if (workflow) checked = validateRunParameters(workflow, request, payload.parameters || {});

  const ctx = await deps.getContext();
  const agent = String(payload.agent || ctx.defaults.agent || '');
  const model = String(payload.model || ctx.defaults.model || '');
  if (!ctx.agents.includes(agent)) throw flowError('agent-unknown', '利用できるAIを選び直してください');
  const readonly = payload.readonly === true;
  if (!readonly && !ctx.workspace.ok) throw flowError('workspace-unavailable', '書き込みありでは実行できません。読み取り専用にするか、リポジトリの公開先を設定してください', { detail: ctx.workspace.reason });
  if (!ctx.tools.agentFlow.ok) throw flowError('tool-missing', 'agent-flow を起動できません', { detail: ctx.tools.agentFlow.summary });

  const values = payload.parameters && typeof payload.parameters === 'object' ? payload.parameters : {};
  const resolvedRequest = templateParameters.applyParameters(request, values);
  const id = runIdNow();
  const logFile = path.join(logDir(), `${id}.log`);
  // workspace.local は **agent-flow が動くホストのファイルシステム**で開くパス（clone 元・
  // recovery ref の保存先として `git -C` に渡る）。Windows から WSL の agent-flow を起こす
  // 構成では、登録した表記（C:\… / \\wsl$\…）のままでは WSL の中で開けないので、埋め込む側
  // （agent-app）が渡す hostPath でホストの表記へ直す。埋め込まない単体版は素通し。
  const hostPath = typeof deps.hostPath === 'function' ? deps.hostPath : (value) => String(value || '');
  const inbox = {
    id,
    title: String(payload.title || '').trim() || resolvedRequest.slice(0, 60),
    request: resolvedRequest,
    submitter: 'agent-app',
    purpose: 'implementation',
    readonly,
    workspace: readonly ? null : { url: ctx.workspace.origin, local: hostPath(deps.root), base: ctx.workspace.branch, path: '', desc: 'workflow' },
    references: [],
    ...(checked ? { plan: checked.plan } : {}),
    ...(source.type === 'pattern' ? { pattern: String(source.pattern || '') } : {}),
    // 定義なし（planner が工程を決める）ときだけ、規模の目安と計画の確認を run ごとに渡す。
    // 未指定なら agent-flow の設定ファイル・既定に従う。保存済みの定義には効かない（工程は決まっている）。
    ...(!workflow && SIZES.has(String(payload.size || '')) ? { size: String(payload.size) } : {}),
    ...(!workflow && typeof payload.planGate === 'boolean' ? { plan_gate: payload.planGate } : {}),
    submitted_at: isoSeconds(),
    // submitter_context は agent-app 自身の覚え書きで、agent-flow は読まない。root は
    // **登録した表記のまま**置く——この画面が「どのリポジトリの実行か」を見分ける鍵で、
    // 登録リポジトリと突き合わせるのはこちら側だから（belongsToRoot）。
    submitter_context: {
      root: deps.root,
      workflow: source.type === 'workflow' ? String(source.id || '') : null,
      digest: checked ? checked.digest : '',
      parameters: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, String(value)])),
      agent,
      model,
      autoApprove: payload.autoApprove !== false,
      source: source.type,
    },
  };
  const file = path.join(busDir(), 'inbox', `${id}.json`);
  flowStore.writeAtomic(file, inbox);
  fs.mkdirSync(logDir(), { recursive: true });
  const args = ['--bus', busDir(), ...configArgs(deps.root, hostPath), '--run-id', id, '--agent-cli', agent, 'run', '--from-inbox'];
  if (model) args.push('--model', model);
  try {
    await deps.startDetached('agent-flow', args, { cwd: deps.root, logFile });
  } catch (err) {
    throw flowError('launch-failed', 'agent-flow を起動できません', { detail: err.message });
  }
  return { runId: id, state: 'launching', request: resolvedRequest, plan: checked ? checked.plan : null, log: { path: logFile } };
}

function runFiles(id) {
  const runId = validRunId(id);
  return {
    id: runId,
    inbox: path.join(busDir(), 'inbox', `${runId}.json`),
    run: path.join(busDir(), 'runs', runId),
    log: path.join(logDir(), `${runId}.log`),
  };
}

// この実行が選択中のリポジトリのものか。見分ける鍵は 2 つある。
//   submitter_context.root … この画面が投函した実行。登録した表記のまま入っている
//   meta.workspace.local   … それ以外（inbox を消された実行・別の投函元）の手掛かり。
//                            **agent-flow が動くホストの表記**なので、hostRoot（登録した
//                            パスをホストの表記へ直したもの）とも突き合わせる。渡されなければ
//                            登録した表記だけで見る（埋め込まない単体版・同じ表記の環境）。
function belongsToRoot(root, files, inbox = readJson(files.inbox), meta = readJson(path.join(files.run, 'meta.json')), hostRoot = '') {
  if (inbox && inbox.submitter === 'agent-app' && inbox.submitter_context
      && String(inbox.submitter_context.root || '') === root) return true;
  if (!meta || !meta.workspace) return false;
  const local = String(meta.workspace.local || '');
  return !!local && (local === root || local === String(hostRoot || ''));
}

function requireRun(root, id, hostRoot = '') {
  const files = runFiles(id);
  const inbox = readJson(files.inbox);
  const meta = readJson(path.join(files.run, 'meta.json'));
  if ((!inbox && !meta) || !belongsToRoot(root, files, inbox, meta, hostRoot)) throw flowError('run-not-found', '実行が見つかりません');
  return { files, inbox, meta };
}

function parseDate(value) {
  const stamp = Date.parse(value || '');
  return Number.isFinite(stamp) ? stamp : 0;
}

function alive(meta, nowSeconds = Date.now() / 1000) {
  if (!meta || TERMINAL.has(String(meta.status || ''))) return null;
  if (typeof meta.orch_lease_until === 'number') return meta.orch_lease_until >= nowSeconds;
  const stamp = parseDate(meta.updated_at || meta.created_at) / 1000;
  return !!stamp && nowSeconds - stamp <= NO_LEASE_GRACE_SECONDS;
}

function claimWinner(dir, nowSeconds) {
  const claims = safeList(dir).filter((name) => name.endsWith('.json')).map((name) => readJson(path.join(dir, name))).filter(Boolean)
    .filter((claim) => !Number(claim.lease_until) || Number(claim.lease_until) >= nowSeconds);
  claims.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0) || String(a.who || '').localeCompare(String(b.who || '')));
  return claims[0] || null;
}

function interactionsOf(runDir) {
  return safeList(path.join(runDir, 'interactions')).flatMap((interactionId) => {
    const dir = path.join(runDir, 'interactions', interactionId);
    const request = readJson(path.join(dir, 'request.json'));
    if (!request) return [];
    const resolution = readJson(path.join(dir, 'resolution.json'));
    const responded = safeList(path.join(dir, 'responses')).some((name) => name.endsWith('.json'));
    const expired = !!parseDate(request.expires_at) && parseDate(request.expires_at) <= Date.now();
    const state = resolution ? 'resolved' : responded ? 'answered' : expired ? 'expired' : 'open';
    return [{
      interactionId,
      nodeId: String(request.node_id || ''),
      mode: String(request.mode || 'input'),
      prompt: String(request.prompt || ''),
      options: Array.isArray(request.options) ? request.options.map(String) : [],
      defaultOption: request.default_option == null ? null : String(request.default_option),
      createdAt: String(request.created_at || ''),
      expiresAt: String(request.expires_at || ''),
      state,
      resolution: resolution ? {
        outcome: String(resolution.outcome || ''),
        answer: resolution.answer && typeof resolution.answer === 'object' ? resolution.answer : {},
        actor: String(resolution.actor || ''),
        resolvedAt: String(resolution.resolved_at || ''),
      } : null,
    }];
  });
}

function topologicalNodes(graph) {
  const source = graph && graph.nodes && typeof graph.nodes === 'object' ? graph.nodes : {};
  const nodes = Object.entries(source).map(([key, value]) => ({ id: String((value && value.id) || key), ...(value || {}) }));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const out = [];
  const seen = new Set();
  function visit(node) {
    if (!node || seen.has(node.id)) return;
    seen.add(node.id);
    (Array.isArray(node.deps) ? node.deps : []).forEach((dep) => visit(byId.get(String(dep))));
    out.push(node);
  }
  nodes.forEach(visit);
  return out;
}

function fileRevision(files) {
  const rows = [];
  function add(file) {
    try { const stat = fs.statSync(file); rows.push(`${path.relative(files.run, file)}:${stat.mtimeMs}:${stat.size}`); } catch { /* 無いものは無視 */ }
  }
  ['meta.json', 'graph.json', 'final.json'].forEach((name) => add(path.join(files.run, name)));
  for (const sub of ['results', 'claims', 'waits', 'interactions']) {
    const root = path.join(files.run, sub);
    const pending = [root];
    while (pending.length) {
      const dir = pending.pop();
      for (const name of safeList(dir)) {
        const file = path.join(dir, name);
        try { if (fs.statSync(file).isDirectory()) pending.push(file); else add(file); } catch { /* 更新中なら次回 */ }
      }
    }
  }
  add(files.inbox);
  return crypto.createHash('sha1').update(rows.sort().join('\n')).digest('hex');
}

function failureOf(meta, state) {
  const detail = String((meta && meta.failure_reason) || '');
  if (!detail && state !== 'cancelled') return null;
  let kind = 'other';
  if (/^\[user-plan\]/.test(detail)) kind = 'plan';
  else if (/^\[verification\]/.test(detail)) kind = 'verification';
  else if (/^\[workset\]|publication/i.test(detail)) kind = 'publication';
  else if (/^\[(agent-flow|agent-control|node-budget)\]/.test(detail)) kind = 'agent';
  else if (/orphaned/i.test(detail)) kind = 'orphaned';
  else if (state === 'cancelled') kind = 'cancelled';
  const message = detail.replace(/^\[[^\]]+\]\s*/, '').split(/[。\n]/).find(Boolean) || (state === 'cancelled' ? '停止しました' : '実行に失敗しました');
  return { kind, message, detail };
}

// 失敗した工程の理由。agent-flow が工程の結果に残した分類（data.error_class か出力の
// [agent-error:…] タグ）を、画面が次の操作を選べる 3 つにまとめる:
//   retry … 一時的（待てば通る） / setup … 認証・環境・上限（人が直すまで同じ失敗） / content … 工程の中身
const ERROR_GROUP = { transient: 'retry', integration: 'retry', auth: 'setup', env: 'setup', quota: 'setup', control: 'setup' };
// setup の失敗は、同じ操作を繰り返しても同じ失敗になる。画面は再実行を勧めず、利用者が次に直すものを 1 行で出す。
// 文言は renderer がエラー文から推し量らないよう、分類と一緒にここで決める（理由の 1 行と重ねないよう、直す操作だけを書く）。
const SETUP_REMEDY = {
  auth: 'AI にログインし直してから再実行してください',
  env: '必要なコマンドが入っているか、接続先に届くかを確かめてから再実行してください',
  quota: '利用上限・レート制限を見直すか、解除されてから再実行してください',
  control: '実行を止める指示（一時停止・停止）を解除してから再実行してください',
};
function nodeErrorOf(result) {
  if (!result || result.status !== 'failed') return null;
  const output = String(result.output || '');
  const tag = /\[agent-error:([a-z]+)\]/.exec(output);
  const cls = String((result.data && typeof result.data === 'object' && result.data.error_class) || (tag && tag[1]) || 'content');
  const message = output.split('\n')
    .map((line) => line.replace(/\[[^\]]+\]\s*/g, '').replace(/^verify=fail:?\s*/i, '').replace(/^\S+ 失敗 \(rc=-?\d+\):?\s*/, '').trim())
    .find(Boolean) || '理由は記録されていません';
  const group = ERROR_GROUP[cls] || 'content';
  return { cls, group, message: message.slice(0, 300), remedy: group === 'setup' ? SETUP_REMEDY[cls] : '' };
}

function deliveryOf(nodes, finalJson) {
  const candidates = [];
  for (const node of nodes) {
    const data = node.data;
    if (!data || typeof data !== 'object') continue;
    if (data.publication && typeof data.publication === 'object') candidates.push(data.publication);
    if (Array.isArray(data.deliveries) && data.deliveries[0] && data.deliveries[0].publication) candidates.push(data.deliveries[0].publication);
  }
  const pub = candidates[0] || (finalJson && finalJson.delivery);
  if (!pub || typeof pub !== 'object') return null;
  return {
    state: String(pub.state || pub.status || 'unknown'),
    branch: String(pub.branch || ''),
    url: String(pub.url || pub.web_url || ''),
    commit: String(pub.commit || pub.sha || ''),
    error: String(pub.error || ''),
    recovery: pub.recovery && typeof pub.recovery === 'object'
      ? { repository: String(pub.recovery.repository || ''), ref: String(pub.recovery.ref || '') } : null,
  };
}

// 「分担と確認」: 誰が何を担当し、別の担当の確認が何を落としたかを、agent-flow が書いた事実だけで数える。
// results/ には差し戻しで置き換えられた工程の結果も残るので、試行の回数と確認の合否の並びがそのまま取れる。
const ROLE_OF = {
  work: 'make', generate: 'make', map: 'make', extract: 'make', retrieve: 'make', split: 'make',
  verify: 'check', judge: 'compare', filter: 'compare', synthesize: 'merge', reduce: 'merge',
  classify: 'route', human: 'person',
};
const ROLE_ORDER = ['route', 'make', 'compare', 'check', 'person', 'merge'];

// 確認の合否は agent-flow の `_normalize_verify` と同じ順に読む（data.ok → 本文の verify=pass/fail）。
// どちらも無い曖昧な出力は不合格——エンジンが完了条件で同じ扱いをする。
function verdictOf(kind, rec) {
  const data = rec.data && typeof rec.data === 'object' ? rec.data : {};
  if (kind === 'human') return rec.status === 'failed' || data.outcome === 'rejected' ? 'fail' : 'pass';
  if (rec.status === 'failed' || data.ok === false) return 'fail';
  if (data.ok === true) return 'pass';
  const output = String(rec.output || '');
  if (/verify\s*=\s*pass/i.test(output)) return 'pass';
  return 'fail';
}

function reworksOf(runDir) {
  let count = 0;
  const dir = path.join(runDir, 'events');
  for (const name of safeList(dir)) {
    if (!name.endsWith('.jsonl')) continue;
    let text = '';
    try { text = fs.readFileSync(path.join(dir, name), 'utf8'); } catch { continue; }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      let event;
      try { event = JSON.parse(line); } catch { continue; }
      if (event.kind === 'verify-fix') count += 1;
      else if (event.kind === 'replan' && event.changes && Array.isArray(event.changes.replaced) && event.changes.replaced.length) count += 1;
    }
  }
  return count;
}

function teamworkOf(runDir, graph, nodes, final) {
  const specs = graph && graph.nodes && typeof graph.nodes === 'object' ? graph.nodes : {};
  const records = [];
  for (const name of safeList(path.join(runDir, 'results'))) {
    if (!name.endsWith('.json')) continue;
    const rec = readJson(path.join(runDir, 'results', name));
    if (!rec || typeof rec !== 'object') continue;
    const id = String(rec.id || name.slice(0, -5));
    const kind = String(rec.kind || (specs[id] && specs[id].kind) || '');
    if (!ROLE_OF[kind]) continue;
    records.push({ id, kind, rec });
  }
  if (!records.length) return null;
  records.sort((a, b) => String(a.rec.finished_at || '').localeCompare(String(b.rec.finished_at || '')));
  const roles = new Map();
  const everyAgent = new Set();
  for (const { kind, rec } of records) {
    const role = ROLE_OF[kind];
    if (!roles.has(role)) roles.set(role, { role, agents: [], attempts: 0, verdicts: [] });
    const row = roles.get(role);
    row.attempts += 1;
    if (role === 'check' || role === 'person') row.verdicts.push(verdictOf(kind, rec));
    if (rec.agent_cli) {
      const label = rec.model ? `${rec.agent_cli} / ${rec.model}` : String(rec.agent_cli);
      if (!row.agents.includes(label)) row.agents.push(label);
      everyAgent.add(String(rec.agent_cli));
    }
  }
  const choices = nodes.filter((node) => (node.kind === 'judge' || node.kind === 'filter') && node.data && typeof node.data === 'object')
    .map((node) => {
      const data = node.data;
      const kept = node.kind === 'judge' ? (data.winner != null && data.winner !== '' ? 1 : 0) : (Array.isArray(data.kept) ? data.kept.length : 0);
      return {
        nodeId: node.id, kind: node.kind, candidates: node.deps.length, kept,
        decidedBy: ['machine', 'judge'].includes(data.decided_by) ? data.decided_by : 'model',
        undecided: Array.isArray(data.undecided) ? data.undecided.length : 0,
      };
    });
  return {
    roles: ROLE_ORDER.filter((role) => roles.has(role)).map((role) => roles.get(role)),
    agents: everyAgent.size,
    reworks: reworksOf(runDir),
    choices,
    verification: final && final.verification && typeof final.verification.state === 'string' ? final.verification.state : null,
  };
}

function inputOf(inbox) {
  const ctx = inbox && inbox.submitter_context ? inbox.submitter_context : {};
  return {
    workflowId: ctx.workflow || null,
    request: String((inbox && inbox.request) || ''),
    parameters: ctx.parameters && typeof ctx.parameters === 'object' ? ctx.parameters : {},
    readonly: !!(inbox && inbox.readonly),
    agent: String(ctx.agent || ''), model: String(ctx.model || ''),
    pattern: String((inbox && inbox.pattern) || ''),
  };
}

function readRun(root, id, hostRoot = '') {
  const { files, inbox, meta } = requireRun(root, id, hostRoot);
  const nowSeconds = Date.now() / 1000;
  if (!meta) {
    const age = Date.now() - parseDate(inbox && inbox.submitted_at);
    const state = age < 60000 ? 'launching' : 'launch-failed';
    return {
      runId: files.id, title: String((inbox && inbox.title) || (inbox && inbox.request) || files.id).slice(0, 60),
      workflowId: inbox && inbox.submitter_context ? inbox.submitter_context.workflow || null : null,
      state, terminal: state === 'launch-failed', createdAt: String((inbox && inbox.submitted_at) || ''), updatedAt: null,
      progress: { done: 0, failed: 0, total: 0 }, waiting: 0, readonly: !!(inbox && inbox.readonly),
      revision: fileRevision(files), request: String((inbox && inbox.request) || ''),
      input: inputOf(inbox), workspace: inbox ? inbox.workspace || null : null,
      failure: state === 'launch-failed' ? { kind: 'agent', message: '起動を確認できません。ログを確認してください', detail: '' } : null,
      alive: null, phase: null, strategy: null, nodes: [], interactions: [], final: null, delivery: null,
      log: { path: files.log }, teamwork: null,
    };
  }
  const graph = readJson(path.join(files.run, 'graph.json')) || {};
  const finalJson = readJson(path.join(files.run, 'final.json'));
  const interactions = interactionsOf(files.run);
  const interactionByNode = new Map(interactions.map((item) => [item.nodeId, item]));
  const rawStatus = String(meta.status || '');
  const terminal = TERMINAL.has(rawStatus);
  const nodes = topologicalNodes(graph).map((spec) => {
    const result = readJson(path.join(files.run, 'results', `${spec.id}.json`));
    const claim = !result && !terminal ? claimWinner(path.join(files.run, 'claims', spec.id), nowSeconds) : null;
    const wait = !result && !claim && !terminal ? readJson(path.join(files.run, 'waits', `${spec.id}.json`)) : null;
    let state = result ? (result.status === 'failed' ? 'failed' : 'done') : claim ? 'claimed'
      : wait && Number(wait.wait_lease_until || 0) >= nowSeconds ? 'parked' : 'pending';
    const interaction = interactionByNode.get(spec.id);
    if (interaction && interaction.state === 'open') state = 'waiting';
    // 終わった実行で結果の無い工程は、前の工程が止まったので動かなかった（回答待ちではない）
    if (terminal && !result) state = 'skipped';
    return {
      id: spec.id, label: String(spec.label || ''), kind: String(spec.kind || 'work'), goal: String(spec.goal || ''),
      deps: Array.isArray(spec.deps) ? spec.deps.map(String) : [], state,
      who: result ? result.who || null : claim ? claim.who || null : wait ? wait.who || null : null,
      agent: result && (result.agent_cli || result.model) ? { cli: String(result.agent_cli || ''), model: String(result.model || '') } : null,
      startedAt: claim ? claim.claimed_at || null : result ? result.started_at || null : null,
      finishedAt: result ? result.finished_at || null : null,
      output: result && typeof result.output === 'string' ? result.output : null,
      error: nodeErrorOf(result),
      data: result && result.data !== undefined ? result.data : null,
      artifacts: result && Array.isArray(result.artifacts) ? result.artifacts.map(String) : [],
      interactionId: interaction ? interaction.interactionId : null,
      dynamic: !!spec.dynamic,
    };
  });
  const progress = {
    done: nodes.filter((node) => node.state === 'done').length,
    failed: nodes.filter((node) => node.state === 'failed').length,
    total: nodes.length,
  };
  let state;
  if (terminal) state = rawStatus === 'canceled' ? 'cancelled' : rawStatus;
  else if (interactions.some((item) => item.state === 'open')) state = 'waiting';
  else if (alive(meta, nowSeconds) === false) state = 'stalled';
  else if (PHASES.has(String(meta.phase || ''))) state = String(meta.phase);
  else if (progress.total && progress.done + progress.failed >= progress.total) state = 'finalizing';
  else state = progress.total ? 'executing' : 'planning';
  const final = finalJson ? {
    finishedAt: String(finalJson.finished_at || ''),
    summary: String(finalJson.summary || ''),
    verification: finalJson.verification && typeof finalJson.verification === 'object' ? finalJson.verification : null,
    ci: finalJson.ci && typeof finalJson.ci === 'object' ? finalJson.ci : null,
  } : null;
  return {
    runId: files.id,
    title: String((inbox && inbox.title) || (inbox && inbox.request) || meta.request || files.id).slice(0, 60),
    workflowId: inbox && inbox.submitter_context ? inbox.submitter_context.workflow || null : null,
    state, terminal, createdAt: String(meta.created_at || (inbox && inbox.submitted_at) || ''), updatedAt: meta.updated_at || null,
    progress, waiting: interactions.filter((item) => item.state === 'open').length,
    readonly: !!((inbox && inbox.readonly) || !meta.workspace),
    revision: fileRevision(files), request: String(meta.request || (inbox && inbox.request) || ''),
    input: inputOf(inbox), workspace: meta.workspace || (inbox && inbox.workspace) || null,
    failure: failureOf(meta, state), alive: terminal ? null : alive(meta, nowSeconds), phase: meta.phase || null,
    strategy: graph.strategy && typeof graph.strategy === 'object' ? graph.strategy : null,
    nodes, interactions, final, delivery: deliveryOf(nodes, finalJson), log: { path: files.log },
    teamwork: terminal ? teamworkOf(files.run, graph, nodes, final) : null,
    attempts: attemptsOf(files.id),
  };
}

// 最初に失敗した工程（履歴で「前回も同じ工程で失敗したか」を見るための手掛かり）
function failedNodeOf(detail) {
  const node = detail.nodes.find((item) => item.state === 'failed');
  return node ? { id: node.id, label: node.label, cls: node.error ? node.error.cls : 'content', message: node.error ? node.error.message : '' } : null;
}

function listRuns(root, limit = 30, hostRoot = '') {
  const ids = new Set();
  for (const name of safeList(path.join(busDir(), 'inbox'))) if (name.endsWith('.json')) ids.add(name.slice(0, -5));
  for (const name of safeList(path.join(busDir(), 'runs'))) ids.add(name);
  const rows = [];
  for (const id of ids) {
    try {
      const detail = readRun(root, id, hostRoot);
      rows.push({
        runId: detail.runId, title: detail.title, workflowId: detail.workflowId, state: detail.state,
        terminal: detail.terminal, createdAt: detail.createdAt, updatedAt: detail.updatedAt,
        progress: detail.progress, waiting: detail.waiting, readonly: detail.readonly,
        request: detail.request, failedNode: failedNodeOf(detail),
      });
    } catch (err) { if (!err || err.code !== 'run-not-found') throw err; }
  }
  return rows.sort((a, b) => parseDate(b.createdAt) - parseDate(a.createdAt)).slice(0, Math.max(1, Math.min(100, Number(limit) || 30)));
}

async function cancel(root, id, reason, capture, hostRoot = '') {
  const detail = readRun(root, id, hostRoot);
  if (detail.terminal) throw flowError('run-terminal', 'この実行はすでに終了しています');
  const result = await capture('agent-flow', ['--bus', busDir(), 'cancel', detail.runId, '--reason', String(reason || '')], { cwd: root, timeoutMs: 30000 });
  if (!result || !result.ok) throw flowError('cancel-failed', '実行を停止できません', { detail: firstLine(result) });
  return { state: 'cancelled' };
}

function respond(root, id, interactionId, raw, hostRoot = '') {
  const { files } = requireRun(root, id, hostRoot);
  const iid = String(interactionId || '');
  if (!/^ix-[a-f0-9]{16}$/.test(iid)) throw flowError('interaction-not-found', '確認項目が見つかりません');
  const dir = path.join(files.run, 'interactions', iid);
  const request = readJson(path.join(dir, 'request.json'));
  if (!request) throw flowError('interaction-not-found', '確認項目が見つかりません');
  const expiresAt = parseDate(request.expires_at);
  if (readJson(path.join(dir, 'resolution.json')) || (expiresAt && expiresAt <= Date.now())) throw flowError('interaction-closed', 'この確認はすでに締め切られています');
  const value = raw && typeof raw === 'object' ? raw : {};
  const comment = String(value.comment || '').trim();
  let answer;
  if (request.mode === 'approval') {
    const decision = String(value.decision || '');
    if (!['approved', 'rejected'].includes(decision)) throw flowError('answer-invalid', '承認または却下を選んでください');
    answer = { decision, ...(comment ? { comment } : {}) };
  } else if (request.mode === 'choice') {
    const option = String(value.option || '');
    if (!(request.options || []).map(String).includes(option)) throw flowError('answer-invalid', '表示された選択肢から選んでください');
    answer = { option, ...(comment ? { comment } : {}) };
  } else if (request.mode === 'input') {
    const text = String(value.text || '').trim();
    if (!text) throw flowError('answer-invalid', '回答を入力してください');
    answer = { text };
  } else throw flowError('answer-invalid', '確認方法が不正です');
  const responseId = `response-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
  const response = { version: 1, interaction_id: iid, response_id: responseId, actor: 'agent-app-user', answer, submitted_at: new Date().toISOString() };
  const body = `${JSON.stringify(response, null, 2)}\n`;
  if (Buffer.byteLength(body) > 64 * 1024) throw flowError('answer-too-large', '回答が長すぎます');
  const responses = path.join(dir, 'responses');
  fs.mkdirSync(responses, { recursive: true });
  const file = path.join(responses, `${responseId}.json`);
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, body, { flag: 'wx' });
  try { fs.linkSync(tmp, file); } finally { fs.unlinkSync(tmp); }
  const interaction = interactionsOf(files.run).find((item) => item.interactionId === iid);
  return { responseId, submittedAt: response.submitted_at, interaction };
}

async function result(root, id, capture, hostRoot = '') {
  const { files } = requireRun(root, id, hostRoot);
  const found = await capture('agent-flow', ['--bus', busDir(), '--run-id', files.id, 'result', '--json'], { cwd: root, timeoutMs: 30000 });
  if (!found || !found.ok) throw flowError('result-failed', '成果を読み取れません', { detail: firstLine(found) });
  try {
    const raw = JSON.parse(String(found.stdout || ''));
    return {
      runId: String(raw.run_id || raw.runId || files.id),
      status: String(raw.status || ''),
      done: !!raw.done,
      request: String(raw.request || ''),
      finalNodes: (Array.isArray(raw.final_nodes) ? raw.final_nodes : raw.finalNodes || []).map((node) => ({
        id: String(node.id || ''), kind: String(node.kind || 'work'), output: String(node.output || ''),
        data: node.data === undefined ? null : node.data,
        artifacts: Array.isArray(node.artifacts) ? node.artifacts.map(String) : [],
      })),
    };
  } catch (err) { throw flowError('result-failed', '成果を読み取れません', { detail: err.message }); }
}

function readLog(root, id, bytes = 16 * 1024, hostRoot = '') {
  const { files } = requireRun(root, id, hostRoot);
  const size = Math.max(1024, Math.min(1024 * 1024, Number(bytes) || 16 * 1024));
  try {
    const stat = fs.statSync(files.log);
    const start = Math.max(0, stat.size - size);
    const fd = fs.openSync(files.log, 'r');
    const buffer = Buffer.alloc(stat.size - start);
    try { fs.readSync(fd, buffer, 0, buffer.length, start); } finally { fs.closeSync(fd); }
    return { path: files.log, tail: buffer.toString('utf8'), truncated: start > 0, exists: true };
  } catch { return { path: files.log, tail: '', truncated: false, exists: false }; }
}

// 工程 1 つ分のセッションログ。agent-flow は工程ごとのログを別に残さないので、実行ログから
// その工程を担当した worker の行（claim してから次の claim まで）を切り出し、工程の出来事
// （events/*.jsonl の node が一致するもの）と時刻順に並べる。担当が分からないときは工程名を含む行だけ。
const LOG_LINE = /^\[([^\]]+)\] \[([^\]]+)\] (.*)$/;
// 失敗した実行を「続きから再実行」する。同じ run-id で agent-flow を起こし直すと、agent-flow が
// 失敗した工程だけを待機に戻してやり直す（済んだ工程は作り直さない）。やり直すと失敗した工程の
// 結果は消えるので、画面が「同じ工程で続けて失敗したか」を数えられるよう、消える前の失敗を
// attempts に控えておく（agent-app 自身の控え。agent-flow は読まない）。
function attemptsFile(id) {
  return path.join(logDir(), `${validRunId(id)}.attempts.json`);
}

function attemptsOf(id) {
  const list = readJson(attemptsFile(id));
  return Array.isArray(list) ? list : [];
}

async function resume(root, id, deps) {
  const detail = readRun(root, id, deps.hostRoot || '');
  if (detail.state !== 'failed') throw flowError('run-not-failed', '失敗した実行だけを続きから再実行できます');
  const ctx = await deps.getContext();
  if (!ctx.tools.agentFlow.ok) throw flowError('tool-missing', 'agent-flow を起動できません', { detail: ctx.tools.agentFlow.summary });
  const agent = String(deps.agent || detail.input.agent || ctx.defaults.agent || '');
  if (!ctx.agents.includes(agent)) throw flowError('agent-unknown', '利用できるAIを選び直してください');
  const failed = detail.nodes.find((node) => node.state === 'failed');
  if (failed) {
    const attempts = attemptsOf(detail.runId);
    attempts.push({ at: isoSeconds(), nodeId: failed.id, cls: failed.error ? failed.error.cls : 'content', message: failed.error ? failed.error.message : '' });
    fs.mkdirSync(logDir(), { recursive: true });
    flowStore.writeAtomic(attemptsFile(detail.runId), attempts);
  }
  const hostPath = typeof deps.hostPath === 'function' ? deps.hostPath : (value) => String(value || '');
  const model = String(deps.model || detail.input.model || '');
  const args = ['--bus', busDir(), ...configArgs(deps.root || root, hostPath), '--run-id', detail.runId, '--agent-cli', agent, 'run'];
  if (model) args.push('--model', model);
  try {
    await deps.startDetached('agent-flow', args, { cwd: root, logFile: path.join(logDir(), `${detail.runId}.log`) });
  } catch (err) {
    throw flowError('launch-failed', 'agent-flow を起動できません', { detail: err.message });
  }
  return { runId: detail.runId, state: 'launching' };
}

function readNodeLog(root, id, nodeId, hostRoot = '') {
  const detail = readRun(root, id, hostRoot);
  const node = detail.nodes.find((item) => item.id === String(nodeId || ''));
  if (!node) throw flowError('node-not-found', '工程が見つかりません');
  const { files } = requireRun(root, id, hostRoot);
  const entries = [];
  const tail = readLog(root, id, 1024 * 1024, hostRoot);
  let capturing = false;
  for (const line of String(tail.tail || '').split('\n')) {
    const m = LOG_LINE.exec(line);
    if (!m) { if (capturing && line.trim()) entries.push({ ts: '', text: line }); continue; }
    const [, ts, who, msg] = m;
    const claim = /claim 成功: (\S+)/.exec(msg);
    if (node.who && who === node.who) {
      if (claim) capturing = claim[1] === node.id;
      if (capturing) entries.push({ ts, text: line });
    } else if (!node.who && msg.includes(node.id)) entries.push({ ts, text: line });
  }
  for (const name of safeList(path.join(files.run, 'events'))) {
    if (!name.endsWith('.jsonl')) continue;
    let body = '';
    try { body = fs.readFileSync(path.join(files.run, 'events', name), 'utf8'); } catch { continue; }
    for (const raw of body.split('\n')) {
      let ev = null;
      try { ev = raw ? JSON.parse(raw) : null; } catch { ev = null; }
      if (!ev || ev.node !== node.id) continue;
      const rest = Object.entries(ev).filter(([key]) => !['ts', 'who', 'kind', 'node'].includes(key)).map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`).join(' ');
      entries.push({ ts: String(ev.ts || ''), text: `[${ev.ts || ''}] [${ev.who || ''}] ${ev.kind || ''}${rest ? ` ${rest}` : ''}` });
    }
  }
  // 時刻の無い行（前の行の続き）は前の行に付いたまま並べる
  let last = '';
  const keyed = entries.map((entry, index) => { if (entry.ts) last = entry.ts; return { ...entry, key: entry.ts || last, index }; });
  keyed.sort((a, b) => a.key.localeCompare(b.key) || a.index - b.index);
  return { nodeId: node.id, text: keyed.map((entry) => entry.text).join('\n'), truncated: !!tail.truncated };
}

function deleteRun(root, id, hostRoot = '') {
  const detail = readRun(root, id, hostRoot);
  if (!detail.terminal) throw flowError('run-active', '実行中です。停止してから削除してください');
  const { files } = requireRun(root, id, hostRoot);
  fs.rmSync(files.run, { recursive: true, force: true });
  for (const file of [files.inbox, path.join(busDir(), 'inbox', 'cancels', `${files.id}.json`), files.log]) {
    try { fs.unlinkSync(file); } catch { /* 無ければよい */ }
  }
  fs.rmSync(path.join(busDir(), 'inbox', 'claims', files.id), { recursive: true, force: true });
  return { deleted: true };
}

async function openDelivery(root, id, hook, hostRoot = '') {
  const detail = readRun(root, id, hostRoot);
  if (!detail.delivery || !['published', 'published-manually'].includes(detail.delivery.state)) throw flowError('delivery-unavailable', '開ける成果ブランチがありません');
  if (typeof hook !== 'function') throw flowError('not-supported', 'このアプリでは成果ブランチを開けません');
  return hook(root, detail.delivery);
}

// 定義なしで動かした実行の工程を、ワークフローの下書きにする（本家の「run を保存」に当たる）。
// agent-flow が決定的に差し込んだ工程（計画の確認・base-sync）と実行時に展開された工程は落とし、
// 落とした工程への依存はその先の依存へつなぎ直す。goal はその実行の依頼に即した文面のままなので、
// 保存前に編集画面で直す前提で返す（保存はしない）。
function planDraft(root, id, hostRoot = '') {
  const { files, inbox } = requireRun(root, id, hostRoot);
  const graph = readJson(path.join(files.run, 'graph.json'));
  if (!graph || !graph.nodes) throw flowError('plan-unavailable', 'まだ工程が決まっていません');
  const specs = topologicalNodes(graph);
  const dropped = new Map();
  const kept = [];
  for (const spec of specs) {
    const kind = String(spec.kind || 'work');
    const drop = spec.dynamic || /^plan-gate(-\d+)?$/.test(spec.id) || !flowModel.VALID_KINDS.has(kind) || !flowModel.ID_RE.test(spec.id);
    if (drop) dropped.set(spec.id, Array.isArray(spec.deps) ? spec.deps.map(String) : []);
    else kept.push(spec);
  }
  const resolveDeps = (deps, seen = new Set()) => deps.flatMap((dep) => {
    if (!dropped.has(dep)) return [dep];
    if (seen.has(dep)) return [];
    seen.add(dep);
    return resolveDeps(dropped.get(dep), seen);
  });
  const label = (goal, fallback) => String(goal || '').split(/\r?\n/).map((line) => line.trim())
    .find((line) => line && !/^\[(scope|out_of_scope)\]/i.test(line))?.slice(0, 40) || fallback;
  const nodes = kept.slice(0, flowModel.MAX_NODES).map((spec) => ({
    id: spec.id,
    label: label(spec.goal, spec.id),
    kind: String(spec.kind || 'work'),
    goal: String(spec.goal || ''),
    deps: [...new Set(resolveDeps(Array.isArray(spec.deps) ? spec.deps.map(String) : []))].filter((dep) => kept.some((node) => node.id === dep)),
    tier: 'auto',
  }));
  if (!nodes.length) throw flowError('plan-unavailable', '保存できる工程がありません');
  const title = String((inbox && inbox.title) || '').trim();
  return {
    version: 2, id: `flow-${Date.now().toString(36)}`, name: title.slice(0, 40), description: '',
    purpose: 'implementation', entry: [], exit: [], nodes, rework: [],
    defaultRequest: String((inbox && inbox.request) || ''),
  };
}

module.exports = {
  planDraft, TERMINAL, NO_LEASE_GRACE_SECONDS, busDir, logDir, runIdNow, catalog, context, start,
  listRuns, readRun, cancel, respond, result, readLog, readNodeLog, resume, deleteRun, openDelivery,
  patterns, alive, claimWinner, interactionsOf, fileRevision, failureOf, deliveryOf,
};
