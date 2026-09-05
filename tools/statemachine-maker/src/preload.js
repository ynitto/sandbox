'use strict';

const { contextBridge, ipcRenderer } = require('electron');

async function invoke(channel, args) {
  const res = await ipcRenderer.invoke(channel, args);
  if (!res || !res.ok) throw new Error(res && res.error ? res.error : `${channel} が失敗しました`);
  return res.data;
}

contextBridge.exposeInMainWorld('api', {
  getConfig: () => invoke('config:get'),
  saveConfig: (config) => invoke('config:save', { config }),
  catalog: () => invoke('catalog:get'),
  addRoot: () => invoke('root:add'),
  removeRoot: (root) => invoke('root:remove', { root }),
  selectRoot: (root) => invoke('root:select', { root }),
  listMachines: (root) => invoke('machine:list', { root }),
  readMachine: (root, machine) => invoke('machine:read', { root, machine }),
  machineExists: (root, machine) => invoke('machine:exists', { root, machine }),
  previewMachine: (spec) => invoke('machine:preview', { spec }),
  saveMachine: (root, spec) => invoke('machine:save', { root, spec }),
  openMachineFolder: (root, machine) => invoke('machine:openFolder', { root, machine }),
  listAgents: (root) => invoke('agents:list', { root }),
  toolStatus: (root) => invoke('tools:status', { root }),
  recordingStart: (payload) => invoke('recording:start', payload),
  recordingStop: (payload) => invoke('recording:stop', payload),
  recordingImport: (payload) => invoke('recording:import', payload),
  recordingState: () => invoke('recording:state'),
  aiStart: (payload) => invoke('ai:start', payload),
  aiStop: (requestId) => invoke('ai:stop', { requestId }),
  aiApply: (payload) => invoke('ai:apply', payload),
  onAiProgress: (cb) => ipcRenderer.on('ai:progress', (_ev, payload) => cb(payload)),
  onAiResult: (cb) => ipcRenderer.on('ai:result', (_ev, payload) => cb(payload)),
  runSnapshot: (root) => invoke('run:snapshot', { root }),
  saveRunSchedule: (root, schedule) => invoke('run:schedule', { root, schedule }),
  setRunDaemon: (root, action) => invoke('run:daemon', { root, action }),
  runLog: (root, identity) => invoke('run:log', { root, identity }),
  runStart: (payload) => invoke('run:start', payload),
  runStop: () => invoke('run:stop'),
  onRunLine: (cb) => ipcRenderer.on('run:line', (_ev, payload) => cb(payload)),
  onRunExit: (cb) => ipcRenderer.on('run:exit', (_ev, payload) => cb(payload)),
});
