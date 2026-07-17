'use strict';

// agent-amigos 制御面。
// - ミッション一覧（バスの読み取り専用ビュー — dashboard からバスへは書かない）
// - ノード予算（node-budget 契約）の表示・上限設定（依頼側・請負側どちらのノードでも同じ）

module.exports = {
  id: 'amigos',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
