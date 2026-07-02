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

  glGroups: (search) => invoke('gitlab:groups', { search }),
  glProjects: (args) => invoke('gitlab:projects', args),
  glLabels: (args) => invoke('gitlab:labels', args),
  glSearch: (args) => invoke('gitlab:search', args),
  glRelated: (target) => invoke('gitlab:related', { target }),
  glDetail: (target) => invoke('gitlab:detail', { target }),

  glComment: (target, body) => invoke('gitlab:comment', { target, body }),
  glUpdateLabels: (target, add, remove) =>
    invoke('gitlab:updateLabels', { target, add, remove }),
  glMerge: (target) => invoke('gitlab:merge', { target }),
  glSetState: (target, event) => invoke('gitlab:setState', { target, event }),

  kiroNeedsList: () => invoke('kiro:needs:list'),
  kiroNeedsRead: (file) => invoke('kiro:needs:read', { file }),
  kiroNeedsFeedback: (file, text) => invoke('kiro:needs:feedback', { file, text }),
  kiroNeedsApprove: (id, project, reason) =>
    invoke('kiro:needs:approve', { id, project, reason }),
  agentSummarizeNeeds: (file) => invoke('agent:summarizeNeeds', { file }),

  agentSummarize: (target) => invoke('agent:summarize', { target }),
  obsidianExport: (target, summary) => invoke('obsidian:export', { target, summary }),
  openExternal: (url) => invoke('shell:openExternal', { url }),
});
