'use strict';

// オーケストレーション制御面。
// - ノード予算（node-budget v2 契約）の集計・トークン推定・配分計算・レート較正
// - エージェント制御（agent-control 契約）の read/write（revision 管理）と status/ 読取
// - エージェント CLI ドロップイン（agent-cli 契約）の棚卸し・検証・編集
//
// いずれもノード横断（マシン単位）の管理面。amigos に間借りしていた node-budget の
// 実装はここへ移管して v2 対応した（amigos:budgetSave は互換のため 1 リリース残す）。

module.exports = {
  id: 'orchestration',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
