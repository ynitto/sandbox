'use strict';

const path = require('path');

const budget = require('./budget');
const homes = require('./homes');
const missions = require('./missions');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  // 一覧（ミッション近似ビュー + ホーム + ノード予算）— renderer の 1 ポーリングで返す
  handle('amigos:overview', () => {
    const cfg = loadConfig();
    const homeList = homes.discoverHomes(cfg);
    const ov = missions.overview(cfg, homeList.map((h) => h.busDir));
    // ミッション → ホームの対応（busDir 一致）。引き受け・依頼の投函先解決に使う
    const byBus = new Map(homeList.filter((h) => h.busDir)
      .map((h) => [path.resolve(h.busDir), h.dir]));
    for (const m of ov.missions) {
      m.home = byBus.get(path.resolve(m.busDir)) || null;
    }
    return { ...ov, homes: homeList, budget: budget.usage(cfg) };
  });
  handle('amigos:budgetSave', (payload) => budget.save(loadConfig(), payload || {}));
  // タスク依頼: ホームの commands/ へ post 指示を投函（常駐デーモンが取り込む）
  handle('amigos:request', (payload) => {
    const p = payload || {};
    let roles = p.roles;
    if (typeof roles === 'string') {
      roles = JSON.parse(roles);
    }
    if (!Array.isArray(roles) || !roles.length) {
      throw new Error('roles には役割ミッション表（JSON 配列）が必要です');
    }
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'post',
      title: String(p.title || ''),
      goal: String(p.goal || ''),
      design: String(p.design || ''),
      mission: p.mission && typeof p.mission === 'object' ? p.mission : undefined,
      roles,
    });
  });
  // 手動引き受け: ホームの commands/ へ claim 指示を投函
  handle('amigos:claim', (payload) => {
    const p = payload || {};
    if (!p.mission || !p.role) throw new Error('mission と role が必要です');
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'claim', mission: String(p.mission), role: String(p.role),
    });
  });
}

module.exports = { registerIpc };
