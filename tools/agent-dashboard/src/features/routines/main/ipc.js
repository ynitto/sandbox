'use strict';

const tmux = require('./tmux');
const send = require('./send');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;

  handle('routineAgent:listSessions', (args = {}) => {
    const cfg = (loadConfig() || {}).routines || {};
    // agent-loop send が作るスタンドアロンセッション名は現在も 'kiro'。
    return tmux.listSessions({
      repo: args.repo || '',
      prefix: args.prefix || cfg.sessionPrefix || 'kiro',
    });
  });

  handle('routineAgent:capture', (args = {}) => tmux.capture({
    target: args.target || args.session || '',
    lines: args.lines,
    repo: args.repo || '',
  }));

  handle('routineAgent:state', (args = {}) => tmux.stateSummary({ repo: args.repo || '' }));

  handle('routineAgent:send', (args = {}) => send.sendPrompt({
    repo: args.repo || '',
    target: args.target || '',
    prompt: args.prompt || '',
  }));
}

module.exports = { registerIpc };
