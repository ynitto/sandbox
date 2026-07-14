'use strict';

// 互換シム: 旧 src/main/*.js パスからの require を維持する。
// 実体は src/base/main/ と src/features/agent-project/main/ に分離済み。
// 新規コードは実体パスを直接 require すること。

module.exports = require('../base/main/ipc');
