'use strict';

// coherence: doc=docs/plans/2026-08-13-agent-dashboard-design-session-design.md, test=tools/agent-dashboard/test/adhoc-flow.test.js

// 設計セッション — 短い要望を、agent-flow がそのまま実行できる設計書まで詰める。
//
// 1 ラウンド = 1 本の短命な設計 run（同梱フロー design-interactive / design-auto）。
// human ノードで park させず、ラウンドの制御は dashboard の決定的コードが持つ:
//   ラウンドへの入力 … 元の要望・現在の設計書・前ラウンドの回答（buildRoundRequest）
//   ラウンドの成果 … 設計書の全文と、末尾の「## 質問」節（splitDesignOutput）
// ラウンド数に上限は無く、やめ時は人が「この設計で実行」を押した時。毎ラウンド完全な
// 設計書が返るので、どこで止めても実装 run へ渡せる。初回に解決したフロー定義は
// セッションの flowSnapshot として保存し、以後のラウンドではその定義だけを使う。
//
// 実行系は adhoc.submit そのもの（新しい実行系・投入契約は作らない）。読み取りも
// バスパーサ（agent-project/main/flow.js）をそのまま使う（C7: 読み手を増やさない）。
// 設計 run はファイルを変更しないため、workspace を渡しても af/<run-id> は push されない
// （agent-flow の「変更が無ければブランチを push しない」契約）。

const fs = require('fs');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');
const designContract = require('../../../base/main/design-contract');
const adhoc = require('./adhoc');

// 手法（mode）と同梱フローの対応。フロー本体は workflows/<id>.json（読み取り専用）。
const MODE_FLOWS = { interactive: 'design-interactive', auto: 'design-auto' };

// 「## 質問」節の見出し。レベルは問わない（生成側が h2 で書く前提だが、h3 でも拾う）。
const QUESTION_HEADING = /^#{1,6}[ \t]*質問[ \t]*$/;
// 書式（必須節と節内の必須項目）の正典は手法カタログ `design-document-format`。
// 数え方は design-contract が唯一の実装で、書式は adhoc が引いて渡す。
const SNAPSHOT_DIR = '.snapshots';
const TARGETS = new Set(['workflow', 'project']);
const SOURCE_MODES = new Set(['new', 'continue', 'use-as-is']);

function cfgOf(config) {
  return (config && config.adhocFlow) || {};
}

function normalizeSession(raw) {
  const session = raw && typeof raw === 'object' ? raw : {};
  const target = TARGETS.has(String(session.target || '')) ? String(session.target) : 'workflow';
  const sourceMode = SOURCE_MODES.has(String(session.sourceMode || ''))
    ? String(session.sourceMode) : 'new';
  const flowSnapshot = normalizeFlowSnapshot(
    session.flowSnapshot || session.designFlow || (session.design && session.design.flow)
  );
  return {
    ...session,
    version: 2,
    target,
    sourceMode,
    sources: Array.isArray(session.sources) ? session.sources : [],
    nodeAssignments: normalizeNodeAssignments(session.nodeAssignments),
    proposal: session.proposal && typeof session.proposal === 'object' ? session.proposal : null,
    application: session.application && typeof session.application === 'object' ? session.application : null,
    ...(flowSnapshot ? { flowSnapshot, designFlow: flowSnapshot } : {}),
  };
}

function normalizeFlowSnapshot(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const source = raw.definition && typeof raw.definition === 'object' ? raw.definition : raw;
  const nodes = Array.isArray(source.nodes) ? source.nodes.map((node) => {
    if (!node || typeof node !== 'object') return null;
    const out = {
      id: String(node.id || '').trim(),
      goal: String(node.goal || '').trim(),
      kind: String(node.kind || 'work').trim() || 'work',
      deps: Array.isArray(node.deps) ? node.deps.map(String) : [],
    };
    if (node.tier) out.tier = String(node.tier);
    if (Array.isArray(node.methods) && node.methods.length) {
      out.methods = node.methods.map((rule) => ({ ...rule }));
    } else if (node.method && typeof node.method === 'object') out.methods = [{ ...node.method }];
    if (node.interaction && typeof node.interaction === 'object') out.interaction = { ...node.interaction };
    if (node.continuation) out.continuation = String(node.continuation);
    return out.id && out.goal ? out : null;
  }).filter(Boolean) : [];
  if (!nodes.length) return null;
  const definition = {
    version: Number(source.version) || 2,
    purpose: String(source.purpose || 'design'),
    libraryVisibility: String(source.libraryVisibility || 'internal'),
    entry: Array.isArray(source.entry) ? source.entry.map(String) : [],
    exit: Array.isArray(source.exit) ? source.exit.map(String) : [],
    nodes,
  };
  if (definition.purpose !== 'design') return null;
  const origin = raw.origin && typeof raw.origin === 'object' ? raw.origin : {};
  const scope = String(raw.scope || origin.scope || '').trim();
  const repository = String(raw.repository || origin.repository || '').trim();
  const id = String(raw.id || '').trim();
  if (!id || !['repository', 'user', 'builtin'].includes(scope)) return null;
  if (scope === 'repository' ? !repository : repository) return null;
  return {
    version: 1,
    id,
    name: String(raw.name || id).trim() || id,
    origin: { scope, repository },
    digest: String(raw.digest || adhoc.digestDefinition(definition)),
    definition,
  };
}

function flowSnapshotFromWorkflow(workflow, origin) {
  const definition = adhoc.workflowDefinition(workflow);
  const scope = String((origin && origin.scope) || workflow._scope || 'builtin').trim();
  const repository = String((origin && origin.repository) || workflow._repository || '').trim();
  return normalizeFlowSnapshot({
    version: 1,
    id: workflow.id,
    name: workflow.name,
    origin: { scope, repository },
    digest: adhoc.digestDefinition(definition),
    definition,
  });
}

function snapshotWorkflowId(sessionId) {
  return `design-session-${String(sessionId).trim()}`;
}

function snapshotWorkflowDir(config, sessionId) {
  return path.join(resolveSessionDir(config), SNAPSHOT_DIR, String(sessionId));
}

function materializeSnapshot(config, sessionId, snapshot) {
  const id = snapshotWorkflowId(sessionId);
  const dir = snapshotWorkflowDir(config, sessionId);
  const definition = snapshot.definition;
  writeJsonAtomic(path.join(dir, `${id}.json`), {
    ...definition,
    id,
    name: snapshot.name,
    purpose: 'design',
    libraryVisibility: definition.libraryVisibility || 'internal',
  });
  return {
    config: {
      ...(config || {}),
      adhocFlow: { ...cfgOf(config), workflowDir: dir },
    },
    id,
  };
}

function resolveFlowSnapshot(config, { selection, designFlow, mode, cwd } = {}) {
  const supplied = selection && typeof selection === 'object'
    ? selection : (designFlow && typeof designFlow === 'object' ? designFlow : {});
  const origin = supplied.origin && typeof supplied.origin === 'object' ? supplied.origin : {};
  const id = String(supplied.id || MODE_FLOWS[String(mode || '')] || '').trim();
  const requestedScope = String(supplied.scope || origin.scope || '').trim();
  const scope = requestedScope || (selection ? '' : 'builtin');
  const repository = String(supplied.repository || origin.repository || '').trim();
  if (!id || (scope && !['repository', 'user', 'builtin'].includes(scope))) {
    throw new Error('設計フローの snapshot を解決できません');
  }
  if (scope === 'repository' && !repository) {
    throw new Error('共有設計フローには repository が必要です');
  }
  const options = {
    cwd: scope === 'repository' ? repository : String(cwd || '').trim(),
    purpose: 'design',
  };
  if (scope) options.scope = scope;
  const workflow = adhoc.loadWorkflow(config, id, options);
  if (!workflow) throw new Error(`設計フローを利用できません: ${id} (${scope})`);
  const resolvedScope = scope || String(workflow._scope || '').trim();
  const resolvedRepository = resolvedScope === 'repository'
    ? String(workflow._repository || repository).trim() : '';
  return flowSnapshotFromWorkflow(workflow, {
    scope: resolvedScope, repository: resolvedRepository,
  });
}

function requiredDesignSections(config, document) {
  return designContract.missingSections(document, adhoc.designDocumentFormat(config));
}

// 節はあるが、中の必須項目（変更対象の強制レイヤー）が無いもの。節の不足と同じ扱いで
// 実装準備完了にしない——文言でしか守られていない契約を実装 run へ流さないため。
function requiredDesignItems(config, document) {
  return designContract.missingItems(document, adhoc.designDocumentFormat(config));
}

// 設計成果が実装へ渡せる形かどうか（節の不足＋節内の項目の不足）。
function designDocumentIssues(config, document) {
  return designContract.documentIssues(document, adhoc.designDocumentFormat(config));
}

// 設計フローのノードへ人が固定したエージェント・モデル（{ nodeId: {tier, agent_cli, model} }）。
// 形だけをここで整える——tier の適格性と候補の実在は plan を組む adhoc 側が検証する。
function normalizeNodeAssignments(raw) {
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

function resolveSessionDir(config) {
  return String(cfgOf(config).designSessionDir || '').trim()
    || agentHomeSubdir('flow', 'design-sessions');
}

function writeJsonAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`);
  fs.renameSync(tmp, file);
}

// --- ラウンドの成果を読む ------------------------------------------------------

// 設計 run の成果 = 設計書 + 次の質問。JSON ではなく Markdown の節規約で受ける
// （長文 Markdown を JSON 文字列へ内包させると小さいモデルが壊す。節規約なら分割に
// 失敗しても全文がそのまま読める＝劣化しても安全）。
function splitDesignOutput(raw) {
  const text = String(raw == null ? '' : raw).replace(/\r\n/g, '\n').trim();
  if (!text) return { document: '', questions: [] };
  const lines = text.split('\n');
  // 見出しは最後のものを採る。設計書の本文が「## 質問」に言及していても、実際の質問節は
  // 末尾にあるため。
  let at = -1;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (QUESTION_HEADING.test(lines[i])) { at = i; break; }
  }
  if (at < 0) return { document: text, questions: [] };
  const questions = [];
  let current = '';
  for (const line of lines.slice(at + 1)) {
    const item = line.match(/^[ \t]*(?:\d+[.)]|[-*])[ \t]+(.*)$/);
    if (item) {
      if (current) questions.push(current.trim());
      current = item[1];
    } else if (current && line.trim()) {
      current += `\n${line.trim()}`;
    }
  }
  if (current) questions.push(current.trim());
  // 見出しはあるのに項目を1つも取れないときは、分割せず全文を設計書として残す。
  // 中途半端に切り落として本文を失うより、読める形で全部渡すほうがましなため。
  if (!questions.length) return { document: text, questions: [] };
  return { document: lines.slice(0, at).join('\n').trim(), questions };
}

// 末端（sink）ノードの出力。集約せず、run の最後に確定した成果をそのまま設計書として使う。
function sinkOutput(run) {
  const nodes = Object.values((run && run.nodes) || {});
  if (!nodes.length) return '';
  const referenced = new Set(nodes.flatMap((node) => node.deps || []));
  const usable = nodes.filter((node) => node.state === 'done' && node.output);
  const sinks = usable.filter((node) => !referenced.has(node.id));
  const pick = (sinks.length ? sinks : usable)
    .sort((a, b) => String(a.finishedAt || '').localeCompare(String(b.finishedAt || '')))
    .pop();
  return pick ? String(pick.output) : '';
}

// --- ラウンドへの入力 ----------------------------------------------------------

function buildRoundRequest({ goal, document, answers, sources } = {}) {
  const answered = (Array.isArray(answers) ? answers : [])
    .map((item) => ({
      question: String((item && item.question) || '').trim(),
      answer: String((item && item.answer) || '').trim(),
    }))
    .filter((item) => item.question && item.answer);
  const parts = [`## 元の要望\n${String(goal || '').trim()}`];
  const normalizedSources = normalizeSources(sources);
  if (normalizedSources.length) {
    parts.push(`## 設計材料\n${normalizedSources.map((source) =>
      `### ${source.kind}: ${source.name}\n${source.content}`).join('\n\n')}`);
  }
  const current = String(document || '').trim();
  if (current) parts.push(`## 現在の設計書\n${current}`);
  if (answered.length) {
    parts.push(`## 前回の質問と回答\n${answered
      .map((item, index) => `${index + 1}. ${item.question}\n   → ${item.answer}`).join('\n')}`);
  }
  return parts.join('\n\n');
}

function normalizeSources(raw) {
  const seen = new Set();
  return (Array.isArray(raw) ? raw : []).flatMap((item, index) => {
    if (!item || typeof item !== 'object') return [];
    const kind = String(item.kind || 'document').trim().toLowerCase();
    const name = String(item.name || `${kind}-${index + 1}`).trim();
    const content = String(item.content || '').trim();
    const id = String(item.id || `${kind}:${name}`).trim();
    if (!content || seen.has(id)) return [];
    seen.add(id);
    return [{ ...item, id, kind, name, content }];
  });
}

// --- セッションの保存 ----------------------------------------------------------

function sessionFile(config, id) {
  const clean = String(id || '').trim();
  if (!/^ds-[a-zA-Z0-9-]{1,60}$/.test(clean)) throw new Error(`設計セッション id が不正です: ${id}`);
  return path.join(resolveSessionDir(config), `${clean}.json`);
}

function newSessionId() {
  const t = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `ds-${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}`
    + `-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`
    + `-${Math.floor(1000 + Math.random() * 9000)}`;
}

function readSession(config, id) {
  try {
    return normalizeSession(JSON.parse(fs.readFileSync(sessionFile(config, id), 'utf8')));
  } catch {
    return null;
  }
}

function saveSession(config, session) {
  const next = normalizeSession({ ...session, updatedAt: new Date().toISOString() });
  writeJsonAtomic(sessionFile(config, next.id), next);
  return next;
}

function deleteSession(config, id) {
  const file = sessionFile(config, id);
  if (!fs.existsSync(file)) return false;
  fs.rmSync(file);
  fs.rmSync(snapshotWorkflowDir(config, id), { recursive: true, force: true });
  return true;
}

// --- ラウンドの実行と回収 ------------------------------------------------------

// run が終端していれば成果を取り込む。done は設計書と質問へ分けて保存し、失敗・中止は
// 理由を残してラウンドを閉じる（セッションは消さない——同じ回答のまま再試行できる）。
function harvest(config, session) {
  if (!session.runId || session.runStatus === 'done') return session;
  const runDir = path.join(adhoc.resolveBusDir(config), 'runs', session.runId);
  if (!fs.existsSync(runDir)) return session;
  const run = adhoc.flow.readRun(runDir);
  const status = String(run.status || '');
  if (!['done', 'failed', 'cancelled', 'canceled'].includes(status)) {
    return session.runStatus === status ? session : saveSession(config, { ...session, runStatus: status });
  }
  if (status !== 'done') {
    return saveSession(config, {
      ...session,
      runStatus: status,
      error: run.failureReason || `設計 run が ${status} で終わりました`,
    });
  }
  const output = sinkOutput(run);
  if (!output) {
    return saveSession(config, { ...session, runStatus: status, error: '設計 run の成果が空でした' });
  }
  const { document, questions } = splitDesignOutput(output);
  const missing = designDocumentIssues(config, document);
  if (missing.length) {
    return saveSession(config, {
      ...session,
      runStatus: status,
      error: `設計成果に必須項目が不足しています: ${missing.join('、')}`,
    });
  }
  const rounds = [...(session.rounds || [])];
  const last = rounds[rounds.length - 1];
  if (last && last.runId === session.runId) {
    last.questions = questions;
    last.finishedAt = new Date().toISOString();
  }
  return saveSession(config, { ...session, runStatus: status, error: '', document, questions, rounds });
}

function summarize(session) {
  return {
    ...session,
    // 一覧は本文を運ばない（セッションが増えるほど画面の初期表示が重くなるため）
    document: undefined,
    documentLength: String(session.document || '').length,
    questionCount: (session.questions || []).length,
  };
}

function listSessions(config) {
  const dir = resolveSessionDir(config);
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
    try {
      const session = normalizeSession(JSON.parse(fs.readFileSync(path.join(dir, entry.name), 'utf8')));
      if (session && session.id) out.push(summarize(session));
    } catch { /* 壊れた1ファイルで一覧全体を壊さない */ }
  }
  return out.sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
}

function getSession(config, id) {
  const session = readSession(config, id);
  if (!session) throw new Error(`設計セッションが見つかりません: ${id}`);
  return harvest(config, session);
}

// セッションを作る（id 省略時）か、回答を添えて次のラウンドを投げる。
function startRound(config, {
  id, cwd, goal, mode, answers, target, sourceMode, sources, document, nodeAssignments,
  selection, designFlow, resolvedFlowSnapshot,
} = {}) {
  const existing = id ? getSession(config, id) : null;
  if (id && !existing) throw new Error(`設計セッションが見つかりません: ${id}`);
  const nextSources = normalizeSources(sources == null ? existing && existing.sources : sources);
  // 割り当ては最初のラウンドで固定し、以降のラウンドも同じ組み合わせで実行する
  // （途中で渡し直せば更新できる）。
  const nextAssignments = nodeAssignments === undefined
    ? (existing && existing.nodeAssignments)
      || normalizeNodeAssignments(selection && selection.nodeAssignments)
      || null
    : normalizeNodeAssignments(nodeAssignments);
  const initialDocument = String(document == null ? (existing && existing.document) || '' : document).trim();
  const request = String(goal || (existing && existing.goal) || initialDocument.slice(0, 200)).trim();
  if (!request) throw new Error('やりたいことを1行でも書いてください');
  const nextSourceMode = SOURCE_MODES.has(String(sourceMode || ''))
    ? String(sourceMode) : (existing && existing.sourceMode) || 'new';
  const selectedMode = String(mode || (nextSourceMode === 'use-as-is' ? 'auto' : '')
    || (existing && existing.mode) || 'interactive');
  const flowId = MODE_FLOWS[selectedMode];
  if (!flowId) throw new Error(`設計の進め方が不正です: ${selectedMode}`);
  const folder = String(cwd == null ? (existing && existing.cwd) || '' : cwd).trim();
  const asked = existing ? existing.questions || [] : [];
  const replies = (Array.isArray(answers) ? answers : []).map((answer) => String(answer || ''));
  const paired = asked.map((question, index) => ({ question, answer: replies[index] || '' }));

  const now = new Date().toISOString();
  const base = existing || {
    version: 2, id: newSessionId(), createdAt: now, rounds: [], document: initialDocument, questions: [],
  };
  // preparation:startDesign が main process で保持している snapshot は、元定義を
  // 再解決せずに使う。公開 IPC の designFlow は従来どおり参照から再解決するため、
  // Renderer が作った定義を信頼する経路にはしない。
  const flowSnapshot = (existing && normalizeFlowSnapshot(
    existing.flowSnapshot || existing.designFlow || (existing.design && existing.design.flow)
  )) || normalizeFlowSnapshot(resolvedFlowSnapshot)
    || resolveFlowSnapshot(config, { selection, designFlow, mode: selectedMode, cwd: folder });
  const materialized = materializeSnapshot(config, base.id, flowSnapshot);

  // cwd は設計フローの参照解決と読み取り専用 reference に使う。submit 側が
  // purpose=design を workspace=null に固定するため、書込 workspace にはならない。
  // snapshot workflow はセッション専用ディレクトリから読むため、元定義の更新・削除にも依存しない。
  const result = adhoc.submit(materialized.config, {
    request: buildRoundRequest({
      goal: request,
      document: existing ? existing.document : initialDocument,
      answers: paired,
      sources: nextSources,
    }),
    cwd: folder,
    purpose: 'design',
    selection: {
      type: 'custom',
      id: materialized.id,
      scope: 'user',
      ...(nextAssignments ? { nodeAssignments: nextAssignments } : {}),
    },
  });

  return saveSession(config, {
    ...base,
    target: TARGETS.has(String(target || '')) ? String(target) : (base.target || 'workflow'),
    sourceMode: nextSourceMode,
    sources: nextSources,
    nodeAssignments: nextAssignments,
    flowSnapshot,
    designFlow: flowSnapshot,
    goal: request,
    mode: selectedMode,
    cwd: folder,
    runId: result.runId,
    runStatus: 'running',
    error: '',
    questions: [],
    rounds: [...(base.rounds || []), {
      runId: result.runId,
      startedAt: now,
      answers: paired.filter((item) => item.answer),
      questions: [],
    }],
  });
}

module.exports = {
  MODE_FLOWS,
  requiredDesignItems,
  designDocumentIssues,
  normalizeSession,
  normalizeFlowSnapshot,
  resolveFlowSnapshot,
  requiredDesignSections,
  resolveSessionDir,
  splitDesignOutput,
  sinkOutput,
  buildRoundRequest,
  normalizeSources,
  listSessions,
  getSession,
  startRound,
  deleteSession,
};
