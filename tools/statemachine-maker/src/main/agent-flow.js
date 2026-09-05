'use strict';

// agent-flow との境界。renderer から bus のパスを受け取らず、登録済み root と id だけで
// 定義の投入・進捗の合成・人の回答を行う。

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flowModel = require('./flow-model');
const flowStore = require('./flow-store');
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
  const inbox = {
    id,
    title: String(payload.title || '').trim() || resolvedRequest.slice(0, 60),
    request: resolvedRequest,
    submitter: 'agent-app',
    purpose: 'implementation',
    readonly,
    workspace: readonly ? null : { url: ctx.workspace.origin, local: deps.root, base: ctx.workspace.branch, path: '', desc: 'workflow' },
    references: [],
    ...(checked ? { plan: checked.plan } : {}),
    ...(source.type === 'pattern' ? { pattern: String(source.pattern || '') } : {}),
    submitted_at: isoSeconds(),
    submitter_context: {
      root: deps.root,
      workflow: source.type === 'workflow' ? String(source.id || '') : null,
      digest: checked ? checked.digest : '',
      parameters: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, String(value)])),
      agent,
      model,
      source: source.type,
    },
  };
  const file = path.join(busDir(), 'inbox', `${id}.json`);
  flowStore.writeAtomic(file, inbox);
  fs.mkdirSync(logDir(), { recursive: true });
  const args = ['--bus', busDir(), '--run-id', id, '--agent-cli', agent, 'run', '--from-inbox'];
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

function belongsToRoot(root, files, inbox = readJson(files.inbox), meta = readJson(path.join(files.run, 'meta.json'))) {
  if (inbox && inbox.submitter === 'agent-app' && inbox.submitter_context
      && String(inbox.submitter_context.root || '') === root) return true;
  return !!(meta && meta.workspace && String(meta.workspace.local || '') === root);
}

function requireRun(root, id) {
  const files = runFiles(id);
  const inbox = readJson(files.inbox);
  const meta = readJson(path.join(files.run, 'meta.json'));
  if ((!inbox && !meta) || !belongsToRoot(root, files, inbox, meta)) throw flowError('run-not-found', '実行が見つかりません');
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

function readRun(root, id) {
  const { files, inbox, meta } = requireRun(root, id);
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
      log: { path: files.log },
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
    return {
      id: spec.id, kind: String(spec.kind || 'work'), goal: String(spec.goal || ''),
      deps: Array.isArray(spec.deps) ? spec.deps.map(String) : [], state,
      who: result ? result.who || null : claim ? claim.who || null : wait ? wait.who || null : null,
      agent: result && (result.agent_cli || result.model) ? { cli: String(result.agent_cli || ''), model: String(result.model || '') } : null,
      startedAt: claim ? claim.claimed_at || null : result ? result.started_at || null : null,
      finishedAt: result ? result.finished_at || null : null,
      output: result && typeof result.output === 'string' ? result.output : null,
      data: result && result.data !== undefined ? result.data : null,
      artifacts: result && Array.isArray(result.artifacts) ? result.artifacts.map(String) : [],
      interactionId: interaction ? interaction.interactionId : null,
      dynamic: !!spec.dynamic,
    };
  });
  const byNode = new Map(nodes.map((node) => [node.id, node]));
  for (const node of nodes) {
    if (node.state === 'pending' && node.deps.some((dep) => byNode.has(dep) && byNode.get(dep).state !== 'done')) node.state = 'waiting';
  }
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
  };
}

function listRuns(root, limit = 30) {
  const ids = new Set();
  for (const name of safeList(path.join(busDir(), 'inbox'))) if (name.endsWith('.json')) ids.add(name.slice(0, -5));
  for (const name of safeList(path.join(busDir(), 'runs'))) ids.add(name);
  const rows = [];
  for (const id of ids) {
    try {
      const detail = readRun(root, id);
      rows.push({
        runId: detail.runId, title: detail.title, workflowId: detail.workflowId, state: detail.state,
        terminal: detail.terminal, createdAt: detail.createdAt, updatedAt: detail.updatedAt,
        progress: detail.progress, waiting: detail.waiting, readonly: detail.readonly,
      });
    } catch (err) { if (!err || err.code !== 'run-not-found') throw err; }
  }
  return rows.sort((a, b) => parseDate(b.createdAt) - parseDate(a.createdAt)).slice(0, Math.max(1, Math.min(100, Number(limit) || 30)));
}

async function cancel(root, id, reason, capture) {
  const detail = readRun(root, id);
  if (detail.terminal) throw flowError('run-terminal', 'この実行はすでに終了しています');
  const result = await capture('agent-flow', ['--bus', busDir(), 'cancel', detail.runId, '--reason', String(reason || '')], { cwd: root, timeoutMs: 30000 });
  if (!result || !result.ok) throw flowError('cancel-failed', '実行を停止できません', { detail: firstLine(result) });
  return { state: 'cancelled' };
}

function respond(root, id, interactionId, raw) {
  const { files } = requireRun(root, id);
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

async function result(root, id, capture) {
  const { files } = requireRun(root, id);
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

function readLog(root, id, bytes = 16 * 1024) {
  const { files } = requireRun(root, id);
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

function deleteRun(root, id) {
  const detail = readRun(root, id);
  if (!detail.terminal) throw flowError('run-active', '実行中です。停止してから削除してください');
  const { files } = requireRun(root, id);
  fs.rmSync(files.run, { recursive: true, force: true });
  for (const file of [files.inbox, path.join(busDir(), 'inbox', 'cancels', `${files.id}.json`), files.log]) {
    try { fs.unlinkSync(file); } catch { /* 無ければよい */ }
  }
  fs.rmSync(path.join(busDir(), 'inbox', 'claims', files.id), { recursive: true, force: true });
  return { deleted: true };
}

async function openDelivery(root, id, hook) {
  const detail = readRun(root, id);
  if (!detail.delivery || !['published', 'published-manually'].includes(detail.delivery.state)) throw flowError('delivery-unavailable', '開ける成果ブランチがありません');
  if (typeof hook !== 'function') throw flowError('not-supported', 'このアプリでは成果ブランチを開けません');
  return hook(root, detail.delivery);
}

module.exports = {
  TERMINAL, NO_LEASE_GRACE_SECONDS, busDir, logDir, runIdNow, catalog, context, start,
  listRuns, readRun, cancel, respond, result, readLog, deleteRun, openDelivery,
  patterns, alive, claimWinner, interactionsOf, fileRevision, failureOf, deliveryOf,
};
