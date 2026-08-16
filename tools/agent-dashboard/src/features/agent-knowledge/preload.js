'use strict';

module.exports = {
  agentKnowledgeDraftsList: (invoke) => (payload) => invoke('agentKnowledge:draftsList', payload || {}),
  agentKnowledgeDraftsApprove: (invoke) => (payload) => invoke('agentKnowledge:draftsApprove', payload || {}),
  agentKnowledgeDraftsDiscard: (invoke) => (payload) => invoke('agentKnowledge:draftsDiscard', payload || {}),
};
