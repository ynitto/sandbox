'use strict';

// agent-project / agent-flow 制御面（agent-stack）。
// base シェルに IPC・preload API・設定既定を登録する。
// agent-project と agent-flow はこのアプリでは一連の制御スタックとして扱う
// （run-id の相互リンク・resubmit/cancel のタスク同期など結合が強いため）。

module.exports = {
  id: 'agent-stack',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
