'use strict';

const cowork = require('./cowork');

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  handle('cowork:overview', (opts) => cowork.overview(loadConfig(), opts || {}));
  handle('cowork:runLoop', ({ itemId, jobId }) => cowork.runLoop(loadConfig(), itemId || jobId));
  handle('cowork:runStateMachine', ({ itemId, machineId, input }) =>
    cowork.runStateMachine(loadConfig(), itemId || machineId, input)
  );
  handle('cowork:generateStateMachine', (payload) => cowork.generateStateMachine(loadConfig(), payload || {}));
  handle('cowork:saveWork', (payload) => cowork.saveWork(loadConfig(), saveConfig, payload || {}));
  // 項目ごとの実行履歴（dashboard 発の実行記録）とリポジトリのログ候補
  handle('cowork:itemLogs', ({ itemId }) => cowork.itemLogs(loadConfig(), itemId));
  handle('cowork:readLog', ({ itemId, file, maxBytes }) =>
    cowork.readLog(loadConfig(), itemId, file, maxBytes)
  );
}

module.exports = { registerIpc };
