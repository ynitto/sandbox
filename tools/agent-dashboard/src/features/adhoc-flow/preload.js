'use strict';

module.exports = {
  adhocFlowOverview: (invoke) => (payload) => invoke('adhocFlow:overview', payload || {}),
  adhocFlowRun: (invoke) => (payload) => invoke('adhocFlow:run', payload || {}),
  adhocFlowInteractionResponse: (invoke) => (payload) => invoke('adhocFlow:interactionResponse', payload || {}),
  adhocFlowSubmit: (invoke) => (payload) => invoke('adhocFlow:submit', payload || {}),
  adhocFlowResubmit: (invoke) => (payload) => invoke('adhocFlow:resubmit', payload || {}),
  adhocFlowCancel: (invoke) => (payload) => invoke('adhocFlow:cancel', payload || {}),
  adhocFlowDeleteRun: (invoke) => (payload) => invoke('adhocFlow:deleteRun', payload || {}),
  adhocFlowSavePreset: (invoke) => (payload) => invoke('adhocFlow:savePreset', payload || {}),
  adhocFlowDeletePreset: (invoke) => (payload) => invoke('adhocFlow:deletePreset', payload || {}),
  adhocFlowSaveWorkflow: (invoke) => (payload) => invoke('adhocFlow:saveWorkflow', payload || {}),
  adhocFlowDeleteWorkflow: (invoke) => (payload) => invoke('adhocFlow:deleteWorkflow', payload || {}),
  adhocFlowCopyMethod: (invoke) => (payload) => invoke('adhocFlow:copyMethod', payload || {}),
  adhocFlowSnapshotSelection: (invoke) => (payload) => invoke('adhocFlow:snapshotSelection', payload || {}),
  adhocFlowPromote: (invoke) => (payload) => invoke('adhocFlow:promote', payload || {}),
  adhocFlowSaveSettings: (invoke) => (payload) => invoke('adhocFlow:saveSettings', payload || {}),
  designSessionList: (invoke) => (payload) => invoke('designSession:list', payload || {}),
  designSessionGet: (invoke) => (payload) => invoke('designSession:get', payload || {}),
  designSessionStart: (invoke) => (payload) => invoke('designSession:start', payload || {}),
  designSessionDelete: (invoke) => (payload) => invoke('designSession:delete', payload || {}),
};
