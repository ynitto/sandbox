'use strict';

module.exports = {
  agentAuditCollect: (invoke) => (payload) => invoke('agentAudit:collect', payload || {}),
  agentAuditUsage: (invoke) => (payload) => invoke('agentAudit:usage', payload || {}),
  agentAuditStats: (invoke) => (payload) => invoke('agentAudit:stats', payload || {}),
  agentAuditDoctor: (invoke) => (payload) => invoke('agentAudit:doctor', payload || {}),
};
