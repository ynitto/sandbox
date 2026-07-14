'use strict';

const { contextBridge, ipcRenderer } = require('electron');
const { loadFeatures } = require('./features');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) {
    throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  }
  return res.data;
}

function buildBaseApi() {
  return {
    getConfig: () => invoke('config:get'),
    saveConfig: (config) => invoke('config:save', { config }),

    gitPull: (dir, force) => invoke('git:pull', { dir, force }),
    gitCommitPush: (dir, message, paths) => invoke('git:commitPush', { dir, message, paths }),
    gitHealth: (dir, refreshRemote = true) => invoke('git:health', { dir, refreshRemote }),
    gitHeal: (dir) => invoke('git:heal', { dir }),
    gitDiff: (args) => invoke('git:diff', args),

    glEnrich: (urls) => invoke('gitlab:enrich', { urls }),
    glProjectIssues: (args) => invoke('gitlab:projectIssues', args),

    openExternal: (url) => invoke('shell:openExternal', { url }),
    openPath: (target) => invoke('shell:openPath', { target }),

    // ディープリンク（agent-dashboard://open?...）からの遷移通知
    onOpenTarget: (cb) => ipcRenderer.on('app:openTarget', (_ev, payload) => cb(payload)),
  };
}

function buildFeatureApi() {
  const api = {};
  for (const feature of loadFeatures()) {
    if (!feature || typeof feature.preloadApi !== 'function') continue;
    const factories = feature.preloadApi() || {};
    for (const [name, factory] of Object.entries(factories)) {
      if (typeof factory !== 'function') continue;
      api[name] = factory(invoke);
    }
  }
  return api;
}

contextBridge.exposeInMainWorld('api', {
  ...buildBaseApi(),
  ...buildFeatureApi(),
});
