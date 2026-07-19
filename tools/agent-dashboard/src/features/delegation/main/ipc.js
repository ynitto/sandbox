'use strict';

// 委譲制御面の IPC。renderer は workload（flow / amigos）を選ぶだけで、同じ封筒で
// 公示（post）→ 落札（award）→ 受入（accept/reject）→ 中止（cancel）を投函できる。
// 変換はアダプタが担い、dashboard はバスへ直接書かない
// （amigos: ホームの commands ドロップ / flow: バスの inbox ドロップ）。

const path = require('path');

const contract = require('./contract');
const amigosAdapter = require('./amigos-adapter');
const flowAdapter = require('./flow-adapter');
const amigosHomes = require('../../amigos/main/homes');
const amigosMissions = require('../../amigos/main/missions');

function resolveFlowBusDirs(cfg) {
  const d = (cfg && cfg.delegation) || {};
  const list = Array.isArray(d.flowBusDirs) ? d.flowBusDirs : [];
  return list.map((p) => String(p || '')).filter(Boolean);
}

// 公示先の解決: workload=amigos はホーム（commands 投函先）、flow はバス（inbox 投函先）。
function routePost(cfg, env, payload) {
  if (env.workload === 'amigos') {
    return amigosHomes.writeCommand(cfg, payload.home, amigosAdapter.toCommand(env));
  }
  return flowAdapter.submitPost(String(payload.busDir || ''), env);
}

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;

  // 一覧: 両エンジンのライフサイクルを正規化ビューに揃えて返す（入札状況込み）。
  // amigos は発見済みホームのバス、flow は設定 delegation.flowBusDirs のバスから読む。
  handle('delegation:list', () => {
    const cfg = loadConfig();
    const items = [];
    const errors = [];
    const now = Date.now() / 1000;

    // amigos: ミッション近似ビュー → 正規化ビュー（assignments/ から入札も読む）
    try {
      const homeList = amigosHomes.discoverHomes(cfg);
      const ov = amigosMissions.overview(cfg, homeList.map((h) => h.busDir));
      const byBus = new Map(
        homeList.filter((h) => h.busDir).map((h) => [path.resolve(h.busDir), h.dir])
      );
      for (const m of ov.missions) {
        try {
          const view = amigosAdapter.toView(m, now);
          view.home = byBus.get(path.resolve(m.busDir)) || null;
          items.push(view);
        } catch (e) {
          errors.push(`amigos ${m.id}: ${e.message}`);
        }
      }
      for (const e of ov.errors || []) errors.push(`amigos: ${e}`);
    } catch (e) {
      errors.push(`amigos: ${e.message}`);
    }

    // flow: 設定されたバスの run → 正規化ビュー
    for (const busDir of resolveFlowBusDirs(cfg)) {
      try {
        for (const view of flowAdapter.listViews(busDir)) {
          view.busDir = busDir;
          items.push(view);
        }
      } catch (e) {
        errors.push(`flow ${busDir}: ${e.message}`);
      }
    }

    items.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    return { items, errors };
  });

  // 公示（post）: renderer の部分ペイロード → 封筒化・検証 → workload でルーティング。
  handle('delegation:post', (payload) => {
    const p = payload || {};
    const env = contract.buildEnvelope('post', p);
    const res = routePost(loadConfig(), env, p);
    return { id: env.id, workload: env.workload, ...res };
  });

  // 落札（award） — owner-picks の確定（amigos のみ）。
  handle('delegation:award', (payload) => {
    const p = payload || {};
    const env = contract.buildEnvelope('award', p);
    return {
      id: env.id,
      workload: env.workload,
      ...amigosHomes.writeCommand(loadConfig(), p.home, amigosAdapter.toCommand(env)),
    };
  });

  // 受入 / 差し戻し（accept / reject） — amigos のみ。
  const acceptReject = (op) => (payload) => {
    const p = payload || {};
    const env = contract.buildEnvelope(op, p);
    return {
      id: env.id,
      workload: env.workload,
      ...amigosHomes.writeCommand(loadConfig(), p.home, amigosAdapter.toCommand(env)),
    };
  };
  handle('delegation:accept', acceptReject('accept'));
  handle('delegation:reject', acceptReject('reject'));

  // 中止（cancel） — 両エンジン対応。
  handle('delegation:cancel', (payload) => {
    const p = payload || {};
    const env = contract.buildEnvelope('cancel', p);
    const cfg = loadConfig();
    if (env.workload === 'amigos') {
      return {
        id: env.id,
        workload: env.workload,
        ...amigosHomes.writeCommand(cfg, p.home, amigosAdapter.toCommand(env)),
      };
    }
    return { id: env.id, workload: env.workload, ...flowAdapter.cancel(String(p.busDir || ''), env) };
  });
}

module.exports = { registerIpc, resolveFlowBusDirs };
