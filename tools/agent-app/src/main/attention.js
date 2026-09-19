'use strict';

// 受信箱の投影（Attention Projector）。
//
// 会話・タスク・ワークフローの正典（sessions/<id>.json、run-history、agent-flow の bus）から、
// 「人が結果を見るべきもの（未読）」と「人の答えが要るもの（要対応）」だけを派生させる。
// ここは純粋関数だけ——状態を持たず、正典の複製も作らない。利用者側に残すのは
// 「その項目の結果をいつまで見たか」（config.json の attentionSeen。store.js）だけ。
//
//   正典（既存の session / run / interaction）
//         ↓ *Sources（正典の要約を「注意の材料」へ写す）
//   source … { key, kind, repo, title, running, resultAt, outcome, interaction, target }
//         ↓ classify（材料 1 つ → action | unread | none）
//   project … 列 2 つの件数と、画面に出す項目
//
// 判定の規則:
//   running（応答中・実行中）           → none（動いているものは邪魔しない）
//   interaction（人の答え待ちが open）   → action（答えが届けば正典側で閉じ、ここからも消える）
//   resultAt があり、見た時刻より新しい  → unread
//   それ以外                             → none
// 失敗（runtime failure）は「見るべき結果」であって「答えが要る状態」ではないので unread。
// 利用者が止めたもの（stopped）は、止めた人がもう見ているので none。

const QUEUES = ['action', 'unread', 'none'];
const MAX_ITEMS = 30;
const MAX_SEEN = 500;

function stamp(value) {
  const at = Date.parse(String(value || ''));
  return Number.isFinite(at) ? at : 0;
}

function text(value, limit = 200) {
  return String(value == null ? '' : value).slice(0, limit);
}

// 会話の「結果」= 末尾が応答メッセージなら、その完了時刻と結末。
//   outcome … done（応答が終わった）| failed（エラーで終わった）| stopped（利用者が止めた）
function conversationResult(messages) {
  const list = Array.isArray(messages) ? messages : [];
  const last = list[list.length - 1];
  if (!last || typeof last !== 'object' || last.role !== 'assistant') return null;
  const outcome = last.stopped ? 'stopped' : last.error ? 'failed' : 'done';
  return { at: text(last.at, 40), outcome };
}

// 会話（session:list の要約）→ 材料。
//   runningIds … 応答中の会話 ID（turn:running と同じ集合）
//   phaseOf    … 会話 ID → tmux の { phase, detail }（追跡していなければ null）
function conversationSources(summaries, { runningIds = [], phaseOf = () => null } = {}) {
  const running = runningIds instanceof Set ? runningIds : new Set(runningIds);
  const out = [];
  for (const s of Array.isArray(summaries) ? summaries : []) {
    if (!s || s.kind !== 'conversation' || s.supersededBy) continue;
    const phase = phaseOf(s.id);
    const interaction = phase && phase.phase === 'attention'
      ? { id: `${s.id}:attention`, mode: 'terminal', prompt: text(phase.detail) }
      : null;
    const result = s.result && typeof s.result === 'object' ? s.result : null;
    out.push({
      key: `conversation:${s.id}`, kind: 'conversation', repo: String(s.repo || ''),
      title: text(s.title, 80) || '（無題）',
      running: running.has(s.id),
      resultAt: result && result.outcome !== 'stopped' ? text(result.at, 40) : '',
      outcome: result ? String(result.outcome || '') : '',
      interaction,
      target: { kind: 'conversation', repo: String(s.repo || ''), id: String(s.id || '') },
    });
  }
  return out;
}

// タスク（run-history の記録）→ 材料。保存名ごとに最新の記録 1 つ。
//   records … runHistory.read の配列 { machine, ok, escalate?, finishedAt, ... }
//   names   … 保存名 → 表示名（定義の name。無ければ保存名）
function taskSources(repo, records, names = {}) {
  const latest = new Map();
  for (const r of Array.isArray(records) ? records : []) {
    if (!r || typeof r !== 'object') continue;
    const machine = String(r.machine || String(r.taskId || '').replace(/^machine:/, '') || '');
    if (!machine || !r.finishedAt) continue;
    const held = latest.get(machine);
    if (!held || stamp(r.finishedAt) > stamp(held.finishedAt)) latest.set(machine, r);
  }
  return [...latest.entries()].map(([machine, r]) => ({
    key: `task:${repo}:${machine}`, kind: 'task', repo: String(repo || ''),
    title: text(names && names[machine], 80) || machine,
    running: false,
    resultAt: text(r.finishedAt, 40),
    outcome: r.ok ? 'done' : r.escalate ? 'escalated' : 'failed',
    interaction: null,
    target: { kind: 'task', repo: String(repo || ''), id: machine },
  }));
}

// ワークフローの実行（agent-flow の readRun / listRuns の行）→ 材料。
//   run … { runId, title, workflowId, state, terminal, createdAt, updatedAt, interactions? }
function workflowSources(repo, runs) {
  const out = [];
  for (const run of Array.isArray(runs) ? runs : []) {
    if (!run || typeof run !== 'object' || !run.runId) continue;
    const open = (Array.isArray(run.interactions) ? run.interactions : []).find((item) => item && item.state === 'open');
    const interaction = open
      ? { id: String(open.interactionId || ''), mode: String(open.mode || 'input'), prompt: text(open.prompt) }
      : null;
    const state = String(run.state || '');
    const terminal = !!run.terminal;
    const outcome = !terminal ? '' : state === 'done' ? 'done' : state === 'cancelled' ? 'stopped' : 'failed';
    out.push({
      key: `workflow:${repo}:${run.runId}`, kind: 'workflow', repo: String(repo || ''),
      title: text(run.title, 80) || String(run.runId),
      running: !terminal && !interaction,
      resultAt: terminal && outcome !== 'stopped' ? text(run.updatedAt || run.createdAt, 40) : '',
      outcome,
      interaction,
      target: { kind: 'workflow', repo: String(repo || ''), id: String(run.workflowId || ''), runId: String(run.runId) },
    });
  }
  return out;
}

// 課題（agent-audit の洞察）→ 材料。人が結果を見るべきものなので受信箱に載せる。
//   insights … audit.insights() の配列（洞察 1 つ = 1 ファイル。改訂されれば updated_at が進み、また未読になる）
//   反証された（review.verdict: refuted）ものと、会話へ渡した（exported）ものは出さない。
function targetOfInsight(ins) {
  const scope = ins && ins.scope && typeof ins.scope === 'object' ? ins.scope : {};
  const target = scope.target && typeof scope.target === 'object' ? scope.target : null;
  const kind = target ? String(target.kind || '') : '';
  const name = target ? String(target.name || '') : '';
  return kind && name ? { kind, name } : null;
}
function insightSources(insights) {
  const out = [];
  for (const ins of Array.isArray(insights) ? insights : []) {
    if (!ins || typeof ins !== 'object' || !ins.id) continue;
    if (ins.exported) continue;
    if (ins.review && ins.review.verdict === 'refuted') continue;
    const target = targetOfInsight(ins);
    const at = text(ins.updated_at || ins.ts || ins.created_at, 40);
    if (!stamp(at)) continue;
    out.push({
      key: `issue:${ins.id}`, kind: 'issue', repo: '',
      title: text(ins.statement, 120) || String(ins.id),
      running: false, resultAt: at, outcome: 'issue', interaction: null,
      target: { kind: 'issue', id: String(ins.id) },
      issue: {
        id: String(ins.id), target, statement: text(ins.statement, 400), kind: String(ins.kind || ''),
        occurrences: Number(ins.occurrences) || 0, confidence: String(ins.confidence || ''),
        evidence: (Array.isArray(ins.observation_ids) ? ins.observation_ids : []).slice(0, 20).map((v) => String(v)),
      },
    });
  }
  return out;
}

// 材料 1 つ → 列。
//   seen  … key → { resultAt }（最後に見た結果の時刻）
//   since … 受信箱を使い始めた時刻。それ以前の結果は、見た記録が無くても既読と扱う
//           （古い会話が一斉に未読になって受信箱が埋まらないように）
function classify(source, seen = {}, since = '') {
  if (!source || typeof source !== 'object') return 'none';
  if (source.running) return 'none';
  if (source.interaction && source.interaction.id) return 'action';
  const resultAt = stamp(source.resultAt);
  if (!resultAt) return 'none';
  const entry = seen && seen[source.key];
  const seenAt = entry ? stamp(entry.resultAt) : 0;
  if (seenAt && seenAt >= resultAt) return 'none';
  if (!seenAt && stamp(since) && resultAt <= stamp(since)) return 'none';
  return 'unread';
}

function sortKey(item) {
  return item.queue === 'action' ? 0 : 1;
}

// 材料の列 → { action, unread, items }。要対応を先に、あとは新しい結果から。
function project(sources, { seen = {}, since = '' } = {}) {
  const items = (Array.isArray(sources) ? sources : [])
    .map((source) => ({ ...source, queue: classify(source, seen, since) }))
    .filter((item) => item.queue !== 'none')
    .sort((a, b) => sortKey(a) - sortKey(b) || stamp(b.resultAt) - stamp(a.resultAt) || a.key.localeCompare(b.key));
  return {
    action: items.filter((item) => item.queue === 'action').length,
    unread: items.filter((item) => item.queue === 'unread').length,
    items: items.slice(0, MAX_ITEMS),
  };
}

// 「見た」を書く。key → { resultAt } の写しを返す（上限を超えたら古い結果から落とす）。
function markSeen(seen, key, resultAt) {
  const next = { ...(seen && typeof seen === 'object' ? seen : {}) };
  const k = String(key || '');
  const at = stamp(resultAt) ? new Date(stamp(resultAt)).toISOString() : '';
  if (!k || !at) return next;
  const held = next[k] ? stamp(next[k].resultAt) : 0;
  if (held >= stamp(at)) return next;
  next[k] = { resultAt: at };
  const keys = Object.keys(next);
  if (keys.length > MAX_SEEN) {
    keys.sort((a, b) => stamp(next[a].resultAt) - stamp(next[b].resultAt));
    for (const drop of keys.slice(0, keys.length - MAX_SEEN)) delete next[drop];
  }
  return next;
}

// 保存値の形を揃える（config.json の attentionSeen）。
function normalizeSeen(raw) {
  const src = raw && typeof raw === 'object' ? raw : {};
  const items = {};
  for (const [key, value] of Object.entries(src.items && typeof src.items === 'object' ? src.items : {})) {
    const at = value && typeof value === 'object' ? stamp(value.resultAt) : 0;
    if (key && at) items[String(key)] = { resultAt: new Date(at).toISOString() };
  }
  return { since: stamp(src.since) ? new Date(stamp(src.since)).toISOString() : '', items };
}

module.exports = {
  QUEUES, MAX_ITEMS, MAX_SEEN,
  conversationResult, conversationSources, taskSources, workflowSources, insightSources, targetOfInsight,
  classify, project, markSeen, normalizeSeen,
};
