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
  instruction: (root, spec) => invoke('instruction:get', { root, spec }),
  runStart: (payload) => invoke('run:start', payload),
  runStop: () => invoke('run:stop'),
  onRunLine: (cb) => ipcRenderer.on('run:line', (_ev, payload) => cb(payload)),
  onRunExit: (cb) => ipcRenderer.on('run:exit', (_ev, payload) => cb(payload)),
  openTerminal: (root) => invoke('shell:openTerminal', { root }),
  copyText: (text) => invoke('clipboard:write', { text }),
});
