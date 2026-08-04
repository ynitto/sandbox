'use strict';

const audit = require('./audit');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  handle('agentAudit:collect', () => audit.collect(loadConfig()));
  handle('agentAudit:usage', ({ period, by } = {}) => audit.usage(loadConfig(), period, by));
  handle('agentAudit:stats', ({ period } = {}) => audit.stats(loadConfig(), period));
  handle('agentAudit:doctor', () => audit.doctor(loadConfig()));
}

module.exports = { registerIpc };
