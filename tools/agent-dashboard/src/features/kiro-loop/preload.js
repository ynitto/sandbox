'use strict';

module.exports = {
  kiroLoopListSessions: (invoke) => (args) => invoke('kiroLoop:listSessions', args || {}),
  kiroLoopCapture: (invoke) => (args) => invoke('kiroLoop:capture', args || {}),
  kiroLoopState: (invoke) => (args) => invoke('kiroLoop:state', args || {}),
  kiroLoopSend: (invoke) => (args) => invoke('kiroLoop:send', args || {}),
};
