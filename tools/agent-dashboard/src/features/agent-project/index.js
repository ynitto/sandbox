'use strict';

// agent-project 制御面（agent-flow 含む）。
// base シェルに IPC・preload API・設定既定を登録する。
// agent-flow も同一 feature に含める（run-id 相互リンク・resubmit/cancel の
// タスク同期など結合が強いため）。

module.exports = {
  id: 'agent-project',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
