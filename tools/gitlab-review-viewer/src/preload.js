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

  glCurrentUser: () => invoke('gitlab:currentUser'),
  glGroups: (search) => invoke('gitlab:groups', { search }),
  glProjects: (args) => invoke('gitlab:projects', args),
  glLabels: (args) => invoke('gitlab:labels', args),
  glSearch: (args) => invoke('gitlab:search', args),
  glRelated: (target) => invoke('gitlab:related', { target }),
  glDetail: (target) => invoke('gitlab:detail', { target }),

  glMRStatus: (target) => invoke('gitlab:mrStatus', { target }),
  glResolveUrl: (url) => invoke('gitlab:resolveUrl', { url }),
  glComment: (target, body) => invoke('gitlab:comment', { target, body }),
  glDeleteIssue: (target) => invoke('gitlab:deleteIssue', { target }),
  glDeleteBranch: (projectId, branch) =>
    invoke('gitlab:deleteBranch', { projectId, branch }),
  glUpdateLabels: (target, add, remove) =>
    invoke('gitlab:updateLabels', { target, add, remove }),
  glMerge: (target) => invoke('gitlab:merge', { target }),
  glSetState: (target, event) => invoke('gitlab:setState', { target, event }),

  agentSummarize: (target) => invoke('agent:summarize', { target }),
  obsidianExport: (target, summary) => invoke('obsidian:export', { target, summary }),
  obsidianExportContent: (payload) => invoke('obsidian:exportContent', payload),
  openExternal: (url) => invoke('shell:openExternal', { url }),

  // ディープリンク（gitlab-review-viewer://open?url=...）の受信。
  // main プロセスが second-instance / open-url で受けた URL を転送してくる。
  onOpenTarget: (cb) => ipcRenderer.on('app:openTarget', (_event, payload) => cb(payload)),
});
