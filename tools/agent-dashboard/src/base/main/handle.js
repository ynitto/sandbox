'use strict';

const { ipcMain } = require('electron');

// すべてのハンドラを {ok, data|error} 形式に揃える（gitlab-review-viewer と同じ）
function handle(channel, fn) {
  ipcMain.handle(channel, async (_event, args) => {
    try {
      return { ok: true, data: await fn(args || {}) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

module.exports = { handle };
