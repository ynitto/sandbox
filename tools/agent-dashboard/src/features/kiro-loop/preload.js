'use strict';

module.exports = {
  kiroLoopListSessions: (invoke) => (args) => invoke('kiroLoop:listSessions', args || {}),
  kiroLoopCapture: (invoke) => (args) => invoke('kiroLoop:capture', args || {}),
};
