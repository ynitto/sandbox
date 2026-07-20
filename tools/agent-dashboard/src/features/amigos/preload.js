'use strict';

module.exports = {
  amigosOverview: (invoke) => () => invoke('amigos:overview', {}),
  amigosBudgetSave: (invoke) => (payload) => invoke('amigos:budgetSave', payload || {}),
  amigosRequest: (invoke) => (payload) => invoke('amigos:request', payload || {}),
  amigosBuildTeam: (invoke) => (payload) => invoke('amigos:buildTeam', payload || {}),
  amigosClaim: (invoke) => (home, mission, role) =>
    invoke('amigos:claim', { home, mission, role }),
  amigosAccept: (invoke) => (home, mission) => invoke('amigos:accept', { home, mission }),
  amigosDeliveryContents: (invoke) => (home, mission) =>
    invoke('amigos:deliveryContents', { home, mission }),
  amigosDeliveryExport: (invoke) => (home, mission) =>
    invoke('amigos:deliveryExport', { home, mission }),
  amigosReject: (invoke) => (home, mission, feedback) =>
    invoke('amigos:reject', { home, mission, feedback }),
};
