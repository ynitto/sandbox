'use strict';

const tmux = require('./tmux');
const send = require('./send');

function registerIpc(ctx) {
  const { handle, loadConfig } = ctx;

  handle('kiroLoop:listSessions', (args = {}) => {
    const cfg = (loadConfig() || {}).kiroLoop || {};
    // 既定接頭辞は 'kiro' — kiro-loop の自動命名（kiro-loop-…）だけでなく、
    // `kiro-loop send` が作る既定セッション（'kiro'）も拾う。
    return tmux.listSessions({
      repo: args.repo || '',
      prefix: args.prefix || cfg.sessionPrefix || 'kiro',
    });
  });

  handle('kiroLoop:capture', (args = {}) => tmux.capture({
    target: args.target || args.session || '',
    lines: args.lines,
    repo: args.repo || '',
  }));

  handle('kiroLoop:state', (args = {}) => tmux.stateSummary({ repo: args.repo || '' }));

  handle('kiroLoop:send', (args = {}) => send.sendPrompt({
    repo: args.repo || '',
    target: args.target || '',
    prompt: args.prompt || '',
  }));
}

module.exports = { registerIpc };
