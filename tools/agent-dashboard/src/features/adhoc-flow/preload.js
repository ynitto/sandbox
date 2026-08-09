'use strict';

module.exports = {
  adhocFlowOverview: (invoke) => (payload) => invoke('adhocFlow:overview', payload || {}),
  adhocFlowRun: (invoke) => (payload) => invoke('adhocFlow:run', payload || {}),
  adhocFlowSubmit: (invoke) => (payload) => invoke('adhocFlow:submit', payload || {}),
  adhocFlowResubmit: (invoke) => (payload) => invoke('adhocFlow:resubmit', payload || {}),
  adhocFlowCancel: (invoke) => (payload) => invoke('adhocFlow:cancel', payload || {}),
  adhocFlowDeleteRun: (invoke) => (payload) => invoke('adhocFlow:deleteRun', payload || {}),
  adhocFlowSavePreset: (invoke) => (payload) => invoke('adhocFlow:savePreset', payload || {}),
  adhocFlowDeletePreset: (invoke) => (payload) => invoke('adhocFlow:deletePreset', payload || {}),
  adhocFlowPromote: (invoke) => (payload) => invoke('adhocFlow:promote', payload || {}),
};
