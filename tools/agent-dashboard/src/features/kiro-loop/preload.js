'use strict';

// kiro-loop が preload 経由で window.api に載せる表面（スタブ）。
// 各値は (invoke) => (...args) => invoke('kiroLoop:...', ...) の形にする。
//
// 例:
//   discover: (invoke) => () => invoke('kiroLoop:discover'),

module.exports = {
  // 実装後にメソッドを足す。空オブジェクトでも preload 合成は壊れない。
};
