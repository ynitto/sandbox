'use strict';

module.exports = {
  coworkOverview: (invoke) => (opts) => invoke('cowork:overview', opts || {}),
  coworkRunLoop: (invoke) => (itemId, parameters) => invoke('cowork:runLoop', { itemId, parameters }),
  coworkRunStateMachine: (invoke) => (itemId, parameters) =>
    invoke('cowork:runStateMachine', { itemId, parameters }),
  coworkGenerateStateMachine: (invoke) => (payload) => invoke('cowork:generateStateMachine', payload),
  coworkSaveWork: (invoke) => (payload) => invoke('cowork:saveWork', payload),
  coworkInspectRoot: (invoke) => (dir) => invoke('cowork:inspectRoot', { dir }),
  coworkPickRoot: (invoke) => () => invoke('cowork:pickRoot', {}),
  coworkSetRoot: (invoke) => (dir, remove) => invoke('cowork:setRoot', { dir, remove }),
  coworkItemLogs: (invoke) => (itemId) => invoke('cowork:itemLogs', { itemId }),
  coworkReadLog: (invoke) => (itemId, file, maxBytes) =>
    invoke('cowork:readLog', { itemId, file, maxBytes }),
};
