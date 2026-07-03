'use strict';

// kiro-flow のバス（<bus>/runs/<run-id>/）を読み取り専用で解析する。
// 状態は kiro-flow 本体と同じく「ファイルの存在」から導出する:
//   results/<id>.json があれば その status（done/failed）
//   claims/<id>/ に lease 内の claim があれば claimed
//   tasks/<id>.json（または graph.json のノード）だけなら pending
// 依存未達の pending は表示上 waiting として区別する（kiro-flow に明示状態は無い）。

const fs = require('fs');
const path = require('path');

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

// claims/<id>/ から勝者を決める。kiro-flow と同じ決定的タイブレーク:
// lease 内の claim のうち (ts, who) が最小の 1 件。
function claimWinner(claimDir, now) {
  const claims = [];
  for (const f of safeList(claimDir)) {
    if (!f.endsWith('.json')) continue;
    const c = readJson(path.join(claimDir, f));
    if (!c || typeof c !== 'object') continue;
    const lease = Number(c.lease_until || 0);
    if (lease && lease < now) continue; // 期限切れは無視（孤児回収）
    claims.push(c);
  }
  if (!claims.length) return null;
  claims.sort((a, b) => (a.ts - b.ts) || String(a.who).localeCompare(String(b.who)));
  return claims[0];
}

// 1 つの run ディレクトリを読み、グラフ＋状態＋進捗のスナップショットにする
function readRun(runDir) {
  const runId = path.basename(runDir);
  const meta = readJson(path.join(runDir, 'meta.json')) || {};
  const graph = readJson(path.join(runDir, 'graph.json')) || {};
  const finalJson = readJson(path.join(runDir, 'final.json'));
  const nodesIn = (graph && typeof graph.nodes === 'object' && graph.nodes) || {};
  const now = Date.now() / 1000;

  const nodes = {};
  for (const [id, spec] of Object.entries(nodesIn)) {
    const result = readJson(path.join(runDir, 'results', `${id}.json`));
    let state = 'pending';
    let who = null;
    let finishedAt = null;
    let output = null;
    let data = null;
    if (result) {
      state = result.status === 'failed' ? 'failed' : 'done';
      who = result.who || null;
      finishedAt = result.finished_at || null;
      output = typeof result.output === 'string' ? result.output : null;
      data = result.data !== undefined ? result.data : null;
    } else {
      const winner = claimWinner(path.join(runDir, 'claims', id), now);
      if (winner) {
        state = 'claimed';
        who = winner.who || null;
      }
    }
    nodes[id] = {
      id,
      goal: String(spec.goal || ''),
      deps: Array.isArray(spec.deps) ? spec.deps.map(String) : [],
      kind: String(spec.kind || 'work'),
      retries: Number(spec.retries || 0),
      state,
      who,
      finishedAt,
      output,
      data,
    };
  }

  // 依存未達の pending は waiting に落とす（可視化用の区別。claim 不能）
  for (const n of Object.values(nodes)) {
    if (n.state !== 'pending') continue;
    const unmet = n.deps.filter((d) => {
      const dep = nodes[d];
      return dep && dep.state !== 'done';
    });
    if (unmet.length) n.state = 'waiting';
  }

  const counts = { done: 0, failed: 0, claimed: 0, pending: 0, waiting: 0 };
  for (const n of Object.values(nodes)) counts[n.state] = (counts[n.state] || 0) + 1;
  const total = Object.keys(nodes).length;

  // gitlab executor の成果（issue_iid / web_url / decision / merged_mrs）を拾い上げる
  const gitlabIssues = [];
  for (const n of Object.values(nodes)) {
    const d = n.data;
    if (d && typeof d === 'object' && !Array.isArray(d) && (d.issue_iid || d.web_url)) {
      gitlabIssues.push({
        nodeId: n.id,
        issueIid: d.issue_iid || null,
        url: d.web_url || '',
        decision: d.decision || null,
        mergedMrs: Array.isArray(d.merged_mrs) ? d.merged_mrs : [],
        state: n.state,
      });
    }
  }

  return {
    runId,
    status: String(meta.status || (finalJson ? 'done' : 'unknown')),
    request: String(meta.request || ''),
    createdAt: meta.created_at || null,
    updatedAt: meta.updated_at || null,
    failureReason: meta.failure_reason || null,
    strategy: graph.strategy || null,
    iteration: Number(graph.iteration || 0),
    nodes,
    counts,
    total,
    progress: total ? (counts.done + counts.failed) / total : 0,
    gitlabIssues,
    final: finalJson
      ? { finishedAt: finalJson.finished_at || null, summary: finalJson.summary || '' }
      : null,
  };
}

// events/*.jsonl を新しい順に最大 limit 件マージして返す
function readRunEvents(runDir, limit = 50) {
  const dir = path.join(runDir, 'events');
  const events = [];
  for (const f of safeList(dir)) {
    if (!f.endsWith('.jsonl')) continue;
    let raw = '';
    try {
      raw = fs.readFileSync(path.join(dir, f), 'utf8');
    } catch {
      continue;
    }
    for (const line of raw.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try {
        const ev = JSON.parse(s);
        if (ev && typeof ev === 'object') events.push(ev);
      } catch {
        /* 壊れた行は無視 */
      }
    }
  }
  events.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return events.slice(0, limit);
}

// バス配下の run を新しい順に一覧する（各 run はサマリのみ）
function listRuns(busDir, limit = 30) {
  const runsDir = path.join(busDir, 'runs');
  const entries = [];
  for (const name of safeList(runsDir)) {
    const runDir = path.join(runsDir, name);
    try {
      if (!fs.statSync(runDir).isDirectory()) continue;
    } catch {
      continue;
    }
    const run = readRun(runDir);
    entries.push(run);
  }
  entries.sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
  return entries.slice(0, limit);
}

module.exports = { readRun, readRunEvents, listRuns };
