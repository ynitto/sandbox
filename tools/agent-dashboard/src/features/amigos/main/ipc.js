'use strict';

const budget = require('./budget');
const missions = require('./missions');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  // 一覧（ミッション近似ビュー + ノード予算）— renderer の 1 ポーリングで両方返す
  handle('amigos:overview', () => {
    const cfg = loadConfig();
    return { ...missions.overview(cfg), budget: budget.usage(cfg) };
  });
  handle('amigos:budgetSave', (payload) => budget.save(loadConfig(), payload || {}));
}

module.exports = { registerIpc };
