'use strict';

const tmux = require('./tmux');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;

  handle('kiroLoop:listSessions', (args = {}) => {
    const cfg = (loadConfig() || {}).kiroLoop || {};
    return tmux.listSessions({
      repo: args.repo || '',
      prefix: args.prefix || cfg.sessionPrefix || 'kiro-loop-',
    });
  });

  handle('kiroLoop:capture', (args = {}) => tmux.capture({
    target: args.target || args.session || '',
    lines: args.lines,
  }));
}

module.exports = { registerIpc };
