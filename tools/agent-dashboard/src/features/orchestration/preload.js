'use strict';

module.exports = {
  orchestrationOverview: (invoke) => () => invoke('orchestration:overview', {}),
  orchestrationBudgetSave: (invoke) => (payload) => invoke('orchestration:budgetSave', payload || {}),
  orchestrationRebalance: (invoke) => () => invoke('orchestration:rebalance', {}),
  orchestrationCalibrate: (invoke) => () => invoke('orchestration:calibrate', {}),
  orchestrationControlSave: (invoke) => (payload) => invoke('orchestration:controlSave', payload || {}),
  orchestrationLifecycle: (invoke) => (payload) => invoke('orchestration:lifecycle', payload || {}),
  orchestrationAgentSave: (invoke) => (payload) => invoke('orchestration:agentSave', payload || {}),
  orchestrationAgentDelete: (invoke) => (payload) => invoke('orchestration:agentDelete', payload || {}),
  orchestrationInstructionsSave: (invoke) => (payload) => invoke('orchestration:instructionsSave', payload || {}),
  orchestrationSkillsInventory: (invoke) => () => invoke('orchestration:skillsInventory', {}),
  orchestrationSessionCommandsSave: (invoke) => (payload) =>
    invoke('orchestration:sessionCommandsSave', payload || {}),
  orchestrationSessionCommandsPreview: (invoke) => (payload) =>
    invoke('orchestration:sessionCommandsPreview', payload || {}),
};
