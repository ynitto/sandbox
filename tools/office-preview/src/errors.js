'use strict';

// code で分岐できるようにしておく。呼び出し側は code を見て「外部アプリで開く」へ逃がせばよい。
//   NOT_ZIP / ENCRYPTED_OR_LEGACY / UNSUPPORTED / TOO_LARGE / BROKEN / TIMEOUT / NO_ELECTRON
class OfficePreviewError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'OfficePreviewError';
    this.code = code;
  }
}

module.exports = { OfficePreviewError };
