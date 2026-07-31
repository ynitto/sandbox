'use strict';

// Cowork 制御面。
// プロジェクト管理とは別に、定期実行（kiro-loop/agent-loop）と
// 定型業務（statemachine-use）の一覧・実行入口を提供する。

module.exports = {
  id: 'cowork',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
