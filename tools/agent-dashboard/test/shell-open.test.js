'use strict';

const assert = require('assert');
const shellActions = require('../src/main/shell-actions');

(async () => {
  await assert.rejects(
    () => shellActions.openPath({ openPath: async () => 'The file does not exist' }, '/missing/file.js'),
    /ファイルを開けません.*The file does not exist/
  );
  console.log('ok - Electron が返す openPath の失敗理由を例外として通知する');
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
