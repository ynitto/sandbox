'use strict';

module.exports = {
  coworkOverview: (invoke) => () => invoke('cowork:overview'),
  coworkRunLoop: (invoke) => (itemId) => invoke('cowork:runLoop', { itemId }),
  coworkRunStateMachine: (invoke) => (itemId, input) =>
    invoke('cowork:runStateMachine', { itemId, input }),
  coworkSaveWork: (invoke) => (payload) => invoke('cowork:saveWork', payload),
};
