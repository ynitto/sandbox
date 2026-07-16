'use strict';

module.exports = {
  coworkOverview: (invoke) => (opts) => invoke('cowork:overview', opts || {}),
  coworkRunLoop: (invoke) => (itemId) => invoke('cowork:runLoop', { itemId }),
  coworkRunStateMachine: (invoke) => (itemId, input) =>
    invoke('cowork:runStateMachine', { itemId, input }),
  coworkSaveWork: (invoke) => (payload) => invoke('cowork:saveWork', payload),
};
