'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  return res.data;
}

const on = (channel) => (cb) => ipcRenderer.on(channel, (_ev, p) => cb(p));

contextBridge.exposeInMainWorld('api', {
  platform: process.platform,
  hostInfo: () => invoke('host:info'),
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
  termOpen: (id, cols, rows) => invoke('term:open', { id, cols, rows }),
  termRestart: (id, cols, rows) => invoke('term:restart', { id, cols, rows }),
  termState: (id) => invoke('term:state', { id }),
  termWatch: (id) => invoke('term:watch', { id }),
  termUnwatch: (id) => invoke('term:unwatch', { id }),
  termKeys: (id, data) => invoke('term:keys', { id, data }),
  termResize: (id, cols, rows) => invoke('term:resize', { id, cols, rows }),
  termKill: (id) => invoke('term:kill', { id }),
  listDir: (repo, rel) => invoke('fs:list', { repo, rel }),
  readFile: (repo, rel) => invoke('fs:read', { repo, rel }),
  findFiles: (repo, query) => invoke('fs:find', { repo, query }),
  changes: (repo) => invoke('git:changes', { repo }),
  fileDiff: (repo, file) => invoke('git:file', { repo, file }),
  openFolder: (repo) => invoke('shell:openFolder', { repo }),
  openFile: (repo, rel) => invoke('shell:openFile', { repo, rel }),
  showFile: (repo, rel) => invoke('shell:showFile', { repo, rel }),
  onTurnStarted: on('turn:started'),
  onTurnLine: on('turn:line'),
  onTurnDone: on('turn:done'),
  onTermScreen: on('term:screen'),
  onTermPhase: on('term:phase'),
});
