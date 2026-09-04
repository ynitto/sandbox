'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  return res.data;
}

contextBridge.exposeInMainWorld('api', {
  getConfig: () => invoke('config:get'),
  saveConfig: (patch) => invoke('config:save', { patch }),
  addRepo: () => invoke('repo:add'),
  removeRepo: (repo) => invoke('repo:remove', { repo }),
  listAgents: (repo) => invoke('agents:list', { repo }),
  listSessions: (repo) => invoke('session:list', { repo }),
  createSession: (payload) => invoke('session:create', payload),
  readSession: (id) => invoke('session:read', { id }),
  updateSession: (id, patch) => invoke('session:update', { id, patch }),
  removeSession: (id) => invoke('session:remove', { id }),
  send: (id, prompt) => invoke('turn:send', { id, prompt }),
  stop: (id) => invoke('turn:stop', { id }),
  running: () => invoke('turn:running'),
  changes: (repo) => invoke('git:changes', { repo }),
  fileDiff: (repo, file) => invoke('git:file', { repo, file }),
  openFolder: (repo) => invoke('shell:openFolder', { repo }),
  onTurnStarted: (cb) => ipcRenderer.on('turn:started', (_ev, p) => cb(p)),
  onTurnLine: (cb) => ipcRenderer.on('turn:line', (_ev, p) => cb(p)),
  onTurnDone: (cb) => ipcRenderer.on('turn:done', (_ev, p) => cb(p)),
});
