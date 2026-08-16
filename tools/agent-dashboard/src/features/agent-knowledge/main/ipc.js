'use strict';

const knowledge = require('./knowledge');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;
  handle('agentKnowledge:draftsList', () => knowledge.listDrafts(loadConfig()));
  handle('agentKnowledge:draftsApprove', ({ file } = {}) => knowledge.approveDraft(loadConfig(), file));
  handle('agentKnowledge:draftsDiscard', ({ file, reason } = {}) =>
    knowledge.discardDraft(loadConfig(), file, reason));
}

module.exports = { registerIpc };
