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
  // opts: { cli, model, readonly, attachments: [{ id, name } | { rel, name }] } — ターンごとに変えられる
  send: (id, prompt, opts) => invoke('turn:send', { id, prompt, ...(opts || {}) }),
  pickAttachments: () => invoke('attach:pick'),
  stageAttachment: (name, bytes) => invoke('attach:stage', { name, bytes }),
  discardAttachment: (id) => invoke('attach:discard', { id }),
  openAttachment: (id, name) => invoke('attach:open', { id, name }),
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
  listWorktrees: (repo) => invoke('wt:list', { repo }),
  createWorktree: (repo, branch, base, name) => invoke('wt:create', { repo, branch, base, name }),
  removeWorktree: (repo, name, opts) => invoke('wt:remove', { repo, name, ...(opts || {}) }),
  listDir: (repo, worktree, rel) => invoke('fs:list', { repo, worktree, rel }),
  readFile: (repo, worktree, rel) => invoke('fs:read', { repo, worktree, rel }),
  findFiles: (repo, worktree, query) => invoke('fs:find', { repo, worktree, query }),
  changes: (repo, worktree, scope) => invoke('git:changes', { repo, worktree, scope }),
  fileDiff: (repo, worktree, file, scope) => invoke('git:file', { repo, worktree, file, scope }),
  openFolder: (repo, worktree) => invoke('shell:openFolder', { repo, worktree }),
  openFile: (repo, worktree, rel) => invoke('shell:openFile', { repo, worktree, rel }),
  showFile: (repo, worktree, rel) => invoke('shell:showFile', { repo, worktree, rel }),
  onTurnStarted: on('turn:started'),
  onTurnLine: on('turn:line'),
  onTurnDone: on('turn:done'),
  onTermScreen: on('term:screen'),
  onTermPhase: on('term:phase'),
});
