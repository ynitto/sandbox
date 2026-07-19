'use strict';

// 委譲制御面。
// agent-flow / agent-amigos への委譲を、エンジン非依存の共通封筒
// （schemas/delegation.schema.json）で扱う。renderer は workload を選ぶだけで、
// 同じ操作（公示→入札→落札→受入→中止）を両エンジンへ投函できる。
// バス・claim プロトコルは統一せず、アダプタがネイティブ形式へ変換する
// （amigos: commands ドロップ / flow: inbox ドロップ）。
// 設計: docs/plans/2026-07-19-delegation-contract-design.md

module.exports = {
  id: 'delegation',
  configDefaults: require('./config'),
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
