'use strict';

module.exports = {
  participationFlowJoin: (invoke) => (payload) => invoke('participation:flowJoin', payload || {}),
};
