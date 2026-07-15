'use strict';

module.exports = {
  cowork: {
    refreshSec: 10,
    loopProvider: 'kiro-loop',
    loopCommand: 'kiro-loop',
    nextLoopProvider: 'agent-loop',
    stateMachineCommand: 'statemachine-use',
    // Flat work list. Each item references a repository already registered in
    // global settings: { id, type: 'loop'|'state-machine', name, repo, schedule, workflow }.
    items: [],
  },
};
