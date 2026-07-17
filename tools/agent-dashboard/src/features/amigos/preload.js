'use strict';

module.exports = {
  amigosOverview: (invoke) => () => invoke('amigos:overview', {}),
  amigosBudgetSave: (invoke) => (payload) => invoke('amigos:budgetSave', payload || {}),
};
