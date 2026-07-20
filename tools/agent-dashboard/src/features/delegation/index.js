'use strict';

// エンジン間の委譲契約（内部機能）。
// agent-flow / agent-amigos への委譲を、エンジン非依存の共通封筒
// （schemas/delegation.schema.json）で扱う。利用者向けの独立画面は持たず、
// 依頼・参加・受け入れはミッション、判断は要対応、進捗は実行から操作する。
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
