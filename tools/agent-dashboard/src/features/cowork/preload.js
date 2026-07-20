'use strict';

module.exports = {
  coworkOverview: (invoke) => (opts) => invoke('cowork:overview', opts || {}),
  coworkRunLoop: (invoke) => (itemId) => invoke('cowork:runLoop', { itemId }),
  coworkRunStateMachine: (invoke) => (itemId, input) =>
    invoke('cowork:runStateMachine', { itemId, input }),
  coworkGenerateStateMachine: (invoke) => (payload) => invoke('cowork:generateStateMachine', payload),
  coworkSaveWork: (invoke) => (payload) => invoke('cowork:saveWork', payload),
  coworkItemLogs: (invoke) => (itemId) => invoke('cowork:itemLogs', { itemId }),
  coworkReadLog: (invoke) => (itemId, file, maxBytes) =>
    invoke('cowork:readLog', { itemId, file, maxBytes }),
};
