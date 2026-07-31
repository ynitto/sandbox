'use strict';

// 委譲封筒 ⇔ agent-amigos ネイティブ形式の変換。
// - toCommand(env): 封筒を amigos-command.schema.json の指示（post/assign/accept/reject/cancel）へ。
//   共通 id は mission_id にそのまま採用（設計 D1 — 対応表を持たない）。
// - toView(summary): missions.readMissionSummary の出力 + バス上の assignments/ を
//   正規化ビュー（delegation.schema.json $defs.delegation_view）へ射影する。
//   入札の可視化（owner-picks の応募者）は assignments/<role>/*.json を assign.py と同じ
//   決定的タイブレーク（(ts, node) 最小・lease 内）で読み解く。

const fs = require('fs');
const path = require('path');

function isObj(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

// 共通コアの値を mission ブロックへ写像（指定された項目だけ載せ、既定は amigos 側に委ねる）。
// engine.amigos.mission の明示上書きが最後に勝つ。
function missionOverrides(env) {
  const m = {};
  const pol = env.policy || {};
  if (pol.assignment) m.assignment_policy = pol.assignment;
  if (pol.staffing) m.staffing_policy = pol.staffing;
  if (pol.staffing_timeout_sec != null) m.staffing_timeout = pol.staffing_timeout_sec;
  if (env.acceptance) m.acceptance = env.acceptance;
  if (env.deadline) m.deadline = env.deadline;
  if (env.workspace) m.workspace = env.workspace;
  const b = env.budget || {};
  const budget = {};
  if (b.execution_minutes) budget.execution_minutes = b.execution_minutes;
  if (b.per_unit_turns != null) budget.per_role_turns = b.per_unit_turns;
  if (Object.keys(budget).length) m.budget = budget;
  const extra = env.engine && env.engine.amigos && env.engine.amigos.mission;
  return isObj(extra) ? { ...m, ...extra } : m;
}

// amigos の post は design を要求するため、省略時は goal + 参照から設計文書を合成する（設計 D）。
function synthDesign(env) {
  const lines = [`# ${env.title || env.goal}`, '', '## 目的', env.goal, ''];
  if (env.references && env.references.length) {
    lines.push('## 参照リポジトリ（読むだけ）');
    for (const r of env.references) lines.push(`- ${r.url || r.path || ''}`);
    lines.push('');
  }
  lines.push('_この設計は agent-dashboard の委譲契約から goal / 参照を元に自動生成されました。_');
  return lines.join('\n');
}

function toCommand(env) {
  switch (env.op) {
    case 'post': {
      const design = (env.design && env.design.trim()) ? env.design : synthDesign(env);
      const mission = missionOverrides(env);
      const rec = {
        command: 'post',
        mission_id: env.id,
        title: env.title || '',
        goal: env.goal || '',
        design,
        roles: env.engine.amigos.roles,
      };
      if (Object.keys(mission).length) rec.mission = mission;
      return rec;
    }
    case 'award':
      return { command: 'assign', mission: env.id, role: env.unit, node: env.node };
    case 'accept':
      return { command: 'accept', mission: env.id };
    case 'reject':
      return { command: 'reject', mission: env.id, feedback: env.feedback };
    case 'cancel':
      return { command: 'cancel', mission: env.id, reason: env.reason || '' };
    default:
      throw new Error(`amigos 未対応の op です: ${env.op}`);
  }
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

// 1 ロールの入札一覧を assignments/<role>/*.json から読む。assign.py と同じ規則:
// lease 内（lease_until >= now）の応募のうち (ts, node) 最小が勝者。roster 確定があれば
// その node が勝者（owner-picks の落札）。lease 失効は expired。
function readBids(missionDir, roleId, assignee, nowSec) {
  const dir = path.join(missionDir, 'assignments', roleId);
  let names;
  try {
    names = fs.readdirSync(dir).filter((n) => n.endsWith('.json') && !n.includes('.tmp.'));
  } catch {
    names = [];
  }
  const raw = [];
  for (const n of names) {
    const c = readJson(path.join(dir, n));
    if (c && c.node != null) raw.push(c);
  }
  const live = raw.filter((c) => Number(c.lease_until || 0) >= nowSec);
  // 決定的タイブレーク（(ts, node) 昇順）— assign.py:winner と同一
  const sorted = [...live].sort((a, b) => {
    const t = Number(a.ts || 0) - Number(b.ts || 0);
    return t || String(a.node).localeCompare(String(b.node));
  });
  const tieWinner = sorted.length ? sorted[0].node : null;
  const winnerNode = assignee || tieWinner; // roster 確定が優先
  const confirmed = !!assignee;
  return raw.map((c) => {
    const expired = Number(c.lease_until || 0) < nowSec;
    let state;
    if (expired) state = 'expired';
    else if (c.node === winnerNode) state = 'winner';
    else state = confirmed ? 'lost' : 'applied';
    return {
      who: String(c.node),
      ts: Number(c.ts || 0),
      claimed_at: c.claimed_at || '',
      lease_until: Number(c.lease_until || 0),
      agent_cli: c.agent_cli || '',
      state,
    };
  }).sort((a, b) => a.ts - b.ts || a.who.localeCompare(b.who));
}

// missions.readMissionSummary の出力（+ summary.dir のバス）を正規化ビューへ。
function toView(summary, nowSec) {
  const now = nowSec == null ? Date.now() / 1000 : nowSec;
  const dir = summary.dir;
  const missionDoc = readJson(path.join(dir, 'mission.json')) || {};
  const ownerPicks = String(missionDoc.assignment_policy || '') === 'owner-picks';

  let bidsOpen = false;
  const units = (summary.roles || []).map((r) => {
    const bids = readBids(dir, r.id, r.node, now);
    let state;
    if (r.done) state = 'done';
    else if (r.state === 'away' || r.state === 'paused') state = 'waiting';
    else if (r.node) state = 'claimed';
    else state = 'open';
    // owner-picks で未確定（roster 未登録）かつ応募がある = 落札待ち
    if (!r.node && bids.some((b) => b.state === 'applied')) bidsOpen = true;
    return {
      unit: r.id,
      kind: r.title || r.id,
      state,
      bids,
      assignee: r.node || '',
    };
  });

  // 応答なし（stale）: roster 確定ノードの heartbeat 途絶。summary は heartbeat 時刻を
  // 持たないため、amigos は「担当が付いた必須ロールで state が明示 away/paused でも done でもない
  // のに status が読めていない」を近似に使う。確実な信号が無い場合は false（過検出しない）。
  const staleUnits = [];

  const phase = summary.phase === 'integrating' ? 'working' : summary.phase;
  const done = units.filter((u) => u.state === 'done').length;
  const open = units.filter((u) => u.state === 'open').length;

  return {
    id: summary.id,
    workload: 'amigos',
    native_id: summary.id,
    phase,
    title: summary.title || summary.id,
    goal: summary.goal || '',
    units,
    bids_open: ownerPicks ? bidsOpen : false,
    stale: false,
    stale_units: staleUnits,
    progress: {
      units_total: units.length,
      units_done: done,
      units_failed: 0,
      units_open: open,
    },
    budget: summary.budget
      ? {
          spent_seconds: summary.budget.spentSeconds || 0,
          limit_minutes: (summary.budget.limitSeconds || 0) / 60,
        }
      : null,
    result:
      summary.phase === 'done'
        ? { status: 'done', accepted: true }
        : summary.phase === 'failed'
        ? { status: 'failed' }
        : {},
    updated_at: summary.postedAt || '',
  };
}

module.exports = { toCommand, toView, readBids, synthDesign, missionOverrides };
