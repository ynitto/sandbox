'use strict';

module.exports = {
  agentAuditCollect: (invoke) => (payload) => invoke('agentAudit:collect', payload || {}),
  agentAuditUsage: (invoke) => (payload) => invoke('agentAudit:usage', payload || {}),
  agentAuditSummary: (invoke) => (payload) => invoke('agentAudit:summary', payload || {}),
  agentAuditStats: (invoke) => (payload) => invoke('agentAudit:stats', payload || {}),
  agentAuditSessions: (invoke) => (payload) => invoke('agentAudit:sessions', payload || {}),
  agentAuditDoctor: (invoke) => (payload) => invoke('agentAudit:doctor', payload || {}),
  agentAuditKnowledge: (invoke) => (payload) => invoke('agentAudit:knowledge', payload || {}),
  agentAuditTasks: (invoke) => (payload) => invoke('agentAudit:tasks', payload || {}),
};
