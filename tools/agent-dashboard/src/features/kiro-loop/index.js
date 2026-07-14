'use strict';

// kiro-loop 制御面の差し込み口（スタブ）。
//
// このディレクトリを他グループのフォーク拡張が実装置き場にする想定。
// プラグインローダまでは用意しない。ソース分離と features/index.js への
// 列挙だけで、上流の base / agent-project 更新を取り込みやすくする。
//
/* 実装の足場:
 *  1. main/ に kiro-loop 状態の読取・操作モジュールを置く
 *  2. main/ipc.js の registerIpc(ctx) で `kiroLoop:*` チャネルを登録
 *  3. preload.js に window.api へ出すメソッドを追加
 *  4. config.js に既定設定を追加
 *  5. renderer は src/renderer/ のサイドバー／タブに
 *     data-feature="kiro-loop" のセクションを足す（README 参照）
 */

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
