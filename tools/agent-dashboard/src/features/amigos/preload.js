'use strict';

module.exports = {
  amigosOverview: (invoke) => () => invoke('amigos:overview', {}),
  amigosBudgetSave: (invoke) => (payload) => invoke('amigos:budgetSave', payload || {}),
  amigosRequest: (invoke) => (payload) => invoke('amigos:request', payload || {}),
  amigosClaim: (invoke) => (home, mission, role) =>
    invoke('amigos:claim', { home, mission, role }),
};
