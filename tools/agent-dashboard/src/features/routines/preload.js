'use strict';

module.exports = {
  routineAgentListSessions: (invoke) => (args) => invoke('routineAgent:listSessions', args || {}),
  routineAgentCapture: (invoke) => (args) => invoke('routineAgent:capture', args || {}),
  routineAgentState: (invoke) => (args) => invoke('routineAgent:state', args || {}),
  routineAgentSend: (invoke) => (args) => invoke('routineAgent:send', args || {}),
  routineAgentQueueMessage: (invoke) => (args) => invoke('routineAgent:queueMessage', args || {}),
  routineAgentQueue: (invoke) => (args) => invoke('routineAgent:queue', args || {}),
};

