'use strict';

// kiro-loop 制御面。
// Phase A: WSL 上の tmux を capture-pane で視聴する（入力なし）。
// 一覧・実行は cowork。端末ビューだけここに閉じる。

module.exports = {
  id: 'kiro-loop',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
