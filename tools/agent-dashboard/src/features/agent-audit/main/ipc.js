'use strict';

const audit = require('./audit');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  handle('agentAudit:collect', () => audit.collect(loadConfig()));
  handle('agentAudit:usage', ({ period, by } = {}) => audit.usage(loadConfig(), period, by));
  handle('agentAudit:summary', ({ period } = {}) => audit.summary(loadConfig(), period));
  handle('agentAudit:stats', ({ period } = {}) => audit.stats(loadConfig(), period));
  handle('agentAudit:sessions', (payload) => audit.sessions(loadConfig(), payload || {}));
  handle('agentAudit:doctor', () => audit.doctor(loadConfig()));
  handle('agentAudit:knowledge', () => audit.knowledge(loadConfig()));
}

module.exports = { registerIpc };
