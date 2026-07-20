'use strict';

const control = require('../../orchestration/main/control');
const participation = require('./participation');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  handle('participation:flowJoin', async (payload) => {
    const cfg = loadConfig();
    const current = control.loadControl(control.resolveControlDir(cfg));
    const lifecycle = String((((current || {}).workloads || {}).flow || {}).lifecycle || 'run');
    if (lifecycle === 'stop') {
      throw new Error('agent-flowは全体設定で停止中です。全体設定で稼働に戻してから参加してください');
    }
    if (lifecycle === 'pause') {
      throw new Error('agent-flowは全体設定で一時停止中です。全体設定で再開してから参加してください');
    }
    return participation.startFlowWorker(payload || {});
  });
}

module.exports = { registerIpc };
