'use strict';

module.exports = {
  id: 'participation',
  configDefaults: {},
  registerIpc(ctx) {
    return require('./main/ipc').registerIpc(ctx);
  },
  preloadApi() {
    return require('./preload');
  },
};
