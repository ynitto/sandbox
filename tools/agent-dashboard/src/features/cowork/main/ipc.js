'use strict';

const cowork = require('./cowork');

function registerIpc(ctx) {
  const { handle, loadConfig, saveConfig } = ctx;
  handle('cowork:overview', () => cowork.overview(loadConfig()));
  handle('cowork:runLoop', ({ itemId, jobId }) => cowork.runLoop(loadConfig(), itemId || jobId));
  handle('cowork:runStateMachine', ({ itemId, machineId, input }) =>
    cowork.runStateMachine(loadConfig(), itemId || machineId, input)
  );
  handle('cowork:saveWork', (payload) => cowork.saveWork(loadConfig(), saveConfig, payload || {}));
}

module.exports = { registerIpc };
