'use strict';

module.exports = {
  coworkOverview: (invoke) => (opts) => invoke('cowork:overview', opts || {}),
  coworkRunLoop: (invoke) => (itemId, parameters, executionChoice) =>
    invoke('cowork:runLoop', { itemId, parameters,
      ...(executionChoice == null ? {} : { executionChoice }) }),
  coworkRunStateMachine: (invoke) => (itemId, parameters, executionChoice) =>
    invoke('cowork:runStateMachine', { itemId, parameters,
      ...(executionChoice == null ? {} : { executionChoice }) }),
  coworkGenerateStateMachine: (invoke) => (payload) => invoke('cowork:generateStateMachine', payload),
  coworkProcedureCatalog: (invoke) => () => invoke('cowork:procedureCatalog', {}),
  coworkProcedurePreview: (invoke) => (payload) => invoke('cowork:procedurePreview', payload),
  coworkProcedureTools: (invoke) => (payload) => invoke('cowork:procedureTools', payload),
  coworkRunAdhoc: (invoke) => (root, prompt) => invoke('cowork:runAdhoc', { root, prompt }),
  coworkSaveWork: (invoke) => (payload) => invoke('cowork:saveWork', payload),
  coworkInspectRoot: (invoke) => (dir) => invoke('cowork:inspectRoot', { dir }),
  coworkPickRoot: (invoke) => () => invoke('cowork:pickRoot', {}),
  coworkSetRoot: (invoke) => (dir, remove) => invoke('cowork:setRoot', { dir, remove }),
  coworkItemLogs: (invoke) => (itemId) => invoke('cowork:itemLogs', { itemId }),
  coworkReadLog: (invoke) => (itemId, file, maxBytes) =>
    invoke('cowork:readLog', { itemId, file, maxBytes }),
};
