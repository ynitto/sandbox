'use strict';

// kiro-loop 制御面の IPC（スタブ）。
// ctx.handle / ctx.loadConfig など base が渡す共通ユーティリティを使う。
//
// 例:
//   function registerIpc(ctx) {
//     const { handle, loadConfig } = ctx;
//     handle('kiroLoop:discover', () => { ... });
//   }

function registerIpc(_ctx) {
  // 未実装。チャネルを登録しない（no-op）。
}

module.exports = { registerIpc };
