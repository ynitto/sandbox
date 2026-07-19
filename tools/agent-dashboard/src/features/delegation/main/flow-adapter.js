'use strict';

// 委譲封筒 ⇔ agent-flow ネイティブ形式の変換。
// - submitPost(busDir, env): 封筒を agent-flow の公式入力契約 inbox/<req-id>.json
//   （= submit_request と同形）として投函する。共通 id をそのまま req-id に採用（設計 D1）。
//   稼働中の daemon が新規要求として拾い、orchestrate してタスクグラフへ分解する。
// - toView(run): flow.readRun の出力を正規化ビューへ射影する。flow は先着 claim（入札即落札）
//   なので bids は勝者 1 件（state=winner）。応答なしは stale フラグで重畳（設計 D8）。
//
// cancel は flow.cancelRun（cmd_cancel と同じ 3 手）へ委ねる — 契約を二重実装しない。

const fs = require('fs');
const path = require('path');

const flow = require('../../agent-project/main/flow');

const TERMINAL = new Set(['done', 'failed', 'canceled']);

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
}

// goal（+ design を「## 設計」節として前置）を request 本文に組む。
function buildRequest(env) {
  const goal = env.goal || '';
  if (env.design && env.design.trim()) {
    return `${goal}\n\n## 設計\n${env.design.trim()}`;
  }
  return goal;
}

// 公式入力契約（submit_request と同形）を inbox/<id>.json へアトミックに書く。
// 既知キーだけを submit_request 準拠で載せ、priority は前方互換の passthrough として置く
// （現行 flow は解釈しない — 将来 gitlab executor の priority:* ラベルへ橋渡しする余地）。
function submitPost(busDir, env) {
  if (!busDir) throw new Error('flow の busDir が必要です');
  const flowEng = (env.engine && env.engine.flow) || {};
  const rec = {
    id: env.id,
    request: buildRequest(env),
    submitter: env.requested_by || 'agent-dashboard',
    workspace: env.workspace || null,
    references: Array.isArray(env.references) ? env.references : [],
    submitted_at: env.requested_at || nowIso(),
  };
  if (flowEng.inherit_from) rec.inherit_from = String(flowEng.inherit_from);
  if (flowEng.executor) rec.executor = String(flowEng.executor);
  if (env.priority && env.priority !== 'normal') rec.priority = env.priority;

  const inbox = path.join(busDir, 'inbox');
  fs.mkdirSync(inbox, { recursive: true });
  const file = path.join(inbox, `${env.id}.json`);
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(tmp, file);
  return { id: env.id, file };
}

function cancel(busDir, env) {
  if (!busDir) throw new Error('flow の busDir が必要です');
  return flow.cancelRun(busDir, env.id, { reason: env.reason || '' });
}

// flow.readRun の node.state（pending/waiting/claimed/done/failed/parked）→ ユニット状態。
function unitState(nodeState) {
  switch (nodeState) {
    case 'done':
      return 'done';
    case 'failed':
      return 'failed';
    case 'claimed':
      return 'claimed';
    case 'parked':
      return 'waiting';
    default:
      return 'open'; // pending / waiting（依存未達）は入札受付前
  }
}

// flow.readRun の出力を正規化ビューへ。
function toView(run) {
  const st = String(run.status || 'unknown');
  const terminal = TERMINAL.has(st);
  let phase;
  if (st === 'canceled') phase = 'cancelled';
  else if (st === 'done') phase = 'done';
  else if (st === 'failed') phase = 'failed';
  else phase = 'working';

  const nodes = Object.values(run.nodes || {});
  const staleUnits = [];
  const units = nodes.map((n) => {
    const state = unitState(n.state);
    if (phase === 'working' && n.state === 'parked') phase = 'waiting';
    const bids = [];
    // 先着 claim: 勝者（実行中 or 完了して result を書いた者）だけを 1 件載せる。
    if (n.who && (n.state === 'claimed' || n.state === 'done' || n.state === 'failed')) {
      bids.push({
        who: String(n.who),
        ts: 0, // readRun は claim の ts を持たない（先着で勝者一意のため不要）
        claimed_at: n.heartbeatAt || '',
        lease_until: Number(n.leaseUntil || 0) || 0,
        state: 'winner',
      });
    }
    return {
      unit: n.id,
      kind: n.kind || 'work',
      state,
      bids,
      assignee: n.who || '',
    };
  });

  // 応答なし（stale）: 非終端かつ orchestrator の生存リースが切れている（孤児の疑い）。
  // フェーズには畳まず重畳フラグで表す（設計 D8）。
  const stale = !terminal && run.alive === false;

  const done = units.filter((u) => u.state === 'done').length;
  const failed = units.filter((u) => u.state === 'failed').length;
  const open = units.filter((u) => u.state === 'open').length;

  return {
    id: run.runId,
    workload: 'flow',
    native_id: run.runId,
    phase,
    title: run.taskId || run.runId,
    goal: run.request || '',
    units,
    bids_open: false, // flow は先着（応募→選定の未確定状態を持たない）
    stale,
    stale_units: staleUnits,
    progress: {
      units_total: units.length,
      units_done: done,
      units_failed: failed,
      units_open: open,
    },
    budget: null, // node-budget 契約が別途ノード側でカバー
    result: run.final ? { status: st, path: '' } : st === 'failed' ? { status: 'failed' } : {},
    updated_at: run.updatedAt || run.createdAt || '',
  };
}

// 1 バスの全 run を正規化ビューにして返す。
function listViews(busDir, limit) {
  const runs = flow.listRuns(busDir, limit == null ? 0 : limit);
  return runs.map((r) => toView(r));
}

module.exports = { submitPost, cancel, toView, listViews, buildRequest, unitState };
