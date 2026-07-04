'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) {
    throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  }
  return res.data;
}

contextBridge.exposeInMainWorld('api', {
  getConfig: () => invoke('config:get'),
  saveConfig: (config) => invoke('config:save', { config }),

  discover: () => invoke('kiro:discover'),
  readProject: (dir) => invoke('kiro:project', { dir }),

  flowRuns: (busDir, limit) => invoke('flow:runs', { busDir, limit }),
  flowRun: (busDir, runId) => invoke('flow:run', { busDir, runId }),
  flowResubmit: (busDir, runId) => invoke('flow:resubmit', { busDir, runId }),
  glFindIssueByToken: (args) => invoke('gitlab:findIssueByToken', args),

  submitFeedback: (file, feedback) => invoke('kiro:feedback', { file, feedback }),
  enqueueTask: (dir, spec) => invoke('kiro:enqueue', { dir, spec }),
  runAction: (args) => invoke('kiro:action', args),

  glEnrich: (urls) => invoke('gitlab:enrich', { urls }),
  glProjectIssues: (args) => invoke('gitlab:projectIssues', args),

  openReview: (target) => invoke('review:open', { target }),
  openExternal: (url) => invoke('shell:openExternal', { url }),
  openPath: (target) => invoke('shell:openPath', { target }),

  // ディープリンク（kiro-projects-viewer://open?...）からの遷移通知
  onOpenTarget: (cb) => ipcRenderer.on('app:openTarget', (_ev, payload) => cb(payload)),
});
