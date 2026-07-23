'use strict';

// 委譲封筒 ⇔ 委譲公示板（agent-board）のファイルレイアウトの変換。
// - submitPost(boardRepoDir, env): 封筒を板の delegations/<id>/post.json として投函する
//   （封筒はそのまま。板が真実・claim プロトコルはエンジンと同一仕様）。落札した各ノードの
//   board デーモンがローカルエンジン（flow inbox / amigos commands）へ引き渡す。
// - award/cancel: delegations/<id>/{award,cancelled}.json を書く（依頼者の書き込み所有パス）。
// - toView(delegationDir): 板のファイルだけから正規化ビュー（delegation_view）を導出する。
//   入札の勝者は (ts, who) 最小の決定的タイブレーク（board.schema.json の bid / エンジンと同一規則）。
//
// dashboard はここでも「ファイルを書くだけ」— git 同期は board デーモン側が担う（バスへ直接
// push しない原則を維持）。契約: schemas/board.schema.json / schemas/delegation.schema.json。

const fs = require('fs');
const path = require('path');

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return null;
  }
}

function writeAtomic(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

function delegationDir(boardRepoDir, id) {
  return path.join(boardRepoDir, 'delegations', String(id));
}

// 公示（post）: 封筒をそのまま delegations/<id>/post.json へ書く（冪等 — 既存なら上書きしない）。
function submitPost(boardRepoDir, env) {
  if (!boardRepoDir) throw new Error('board のリポジトリパスが必要です');
  const dir = delegationDir(boardRepoDir, env.id);
  const file = path.join(dir, 'post.json');
  if (fs.existsSync(file)) return { id: env.id, file, duplicate: true };
  writeAtomic(file, env);
  return { id: env.id, file };
}

// 落札確定（owner-picks）: delegations/<id>/award.json（依頼者のみ書く）。
function award(boardRepoDir, env) {
  if (!boardRepoDir) throw new Error('board のリポジトリパスが必要です');
  const file = path.join(delegationDir(boardRepoDir, env.id), 'award.json');
  writeAtomic(file, { node: env.node, awarded_by: 'agent-dashboard', awarded_at: nowIso() });
  return { id: env.id, file };
}

// 中止: delegations/<id>/cancelled.json（依頼者のみ書く）。
function cancel(boardRepoDir, env) {
  if (!boardRepoDir) throw new Error('board のリポジトリパスが必要です');
  const file = path.join(delegationDir(boardRepoDir, env.id), 'cancelled.json');
  writeAtomic(file, { reason: env.reason || '', cancelled_by: 'agent-dashboard', cancelled_at: nowIso() });
  return { id: env.id, file };
}

// bids/<who>.json を正規化。勝者は lease 内の (ts, who) 最小（agent-board の winner と同一規則）。
function readBids(dir, awardNode, nowSec) {
  const bd = path.join(dir, 'bids');
  let names = [];
  try {
    names = fs.readdirSync(bd).filter((n) => n.endsWith('.json'));
  } catch (e) {
    return { bids: [], winner: null };
  }
  const live = [];
  const all = [];
  for (const n of names) {
    const info = readJson(path.join(bd, n));
    if (!info || !info.who) continue;
    const rec = {
      who: String(info.who),
      ts: Number(info.ts) || 0,
      claimed_at: info.claimed_at || '',
      lease_until: Number(info.lease_until) || 0,
      state: 'applied',
    };
    if (info.agent_cli) rec.agent_cli = String(info.agent_cli);
    all.push(rec);
    if (rec.lease_until >= nowSec) live.push(rec);
  }
  // 決定的タイブレーク: (ts, who) 最小。award があればそれが確定担当。
  live.sort((a, b) => (a.ts - b.ts) || a.who.localeCompare(b.who));
  const winner = awardNode || (live.length ? live[0].who : null);
  for (const rec of all) {
    if (rec.lease_until < nowSec) rec.state = 'expired';
    else if (rec.who === winner) rec.state = 'winner';
    else rec.state = awardNode ? 'applied' : 'lost';
  }
  return { bids: all, winner };
}

function derivePhase(dir, winner, statuses, result, cancelled) {
  if (cancelled) return 'cancelled';
  if (result) return result.status === 'failed' ? 'failed' : 'done';
  if (winner && statuses[winner]) {
    const st = String(statuses[winner].state || '');
    if (['waiting', 'reviewing'].includes(st)) return st;
    if (st === 'away') return 'waiting';
    return 'working';
  }
  if (winner) return 'working';
  return 'open';
}

// 板の 1 委譲ディレクトリ → 正規化ビュー（delegation_view）。
function toView(dir, nowSec) {
  const now = nowSec == null ? Date.now() / 1000 : nowSec;
  const env = readJson(path.join(dir, 'post.json')) || {};
  const id = env.id || path.basename(dir);
  const awardRec = readJson(path.join(dir, 'award.json'));
  const result = readJson(path.join(dir, 'result.json'));
  const cancelled = readJson(path.join(dir, 'cancelled.json'));
  const { bids, winner } = readBids(dir, awardRec && awardRec.node, now);

  const statuses = {};
  try {
    const sd = path.join(dir, 'status');
    for (const n of fs.readdirSync(sd)) {
      if (!n.endsWith('.json')) continue;
      const rec = readJson(path.join(sd, n));
      if (rec && rec.who) statuses[rec.who] = rec;
    }
  } catch (e) { /* status 無し */ }

  const phase = derivePhase(dir, winner, statuses, result, cancelled);
  const unitState = { open: 'open', working: 'claimed', waiting: 'waiting',
    reviewing: 'claimed', done: 'done', failed: 'failed', cancelled: 'done' }[phase] || 'open';
  const assignment = (env.policy && env.policy.assignment) || 'first-come';

  const view = {
    id,
    workload: env.workload || 'flow',
    native_id: id,
    phase,
    title: env.title || '',
    goal: env.goal || '',
    target: 'board',
    units: [{ unit: id, kind: env.workload || 'flow', state: unitState, bids,
      assignee: winner || undefined }],
    bids_open: assignment === 'owner-picks' && !awardRec && bids.some((b) => b.state !== 'expired'),
    stale: false,
    progress: {
      units_total: 1,
      units_done: phase === 'done' ? 1 : 0,
      units_failed: phase === 'failed' ? 1 : 0,
      units_open: phase === 'open' ? 1 : 0,
    },
    budget: null,
    updated_at: nowIso(),
  };
  if (result) {
    view.result = { status: result.status || (phase === 'failed' ? 'failed' : 'done'),
      by: result.winner || result.resolved_by, ts: result.resolved_at };
  }
  return view;
}

// board リポジトリ配下の全委譲を正規化ビューにして返す。
function listViews(boardRepoDir, nowSec) {
  const root = path.join(boardRepoDir, 'delegations');
  let names = [];
  try {
    names = fs.readdirSync(root).filter((n) => fs.statSync(path.join(root, n)).isDirectory());
  } catch (e) {
    return [];
  }
  return names.map((n) => toView(path.join(root, n), nowSec));
}

module.exports = { submitPost, award, cancel, toView, listViews, readBids };
