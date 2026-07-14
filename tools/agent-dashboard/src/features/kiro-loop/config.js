'use strict';

// kiro-loop 制御面の既定設定（プレースホルダ）。
// 他グループが実装を入れるときは、ここに UI / IPC が読むキーを足す。
// 例: { kiroLoop: { roots: [], command: 'kiro-loop', refreshSec: 5 } }

module.exports = {
  // キーを空のままにしておけば base の DEFAULT_CONFIG に影響しない。
  // 実装時はコメントを外して既定値を置く。
  // kiroLoop: {
  //   roots: [],
  //   command: 'kiro-loop',
  //   refreshSec: 5,
  // },
};
