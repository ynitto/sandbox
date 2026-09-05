'use strict';

// statemachine-maker の検証済みドメインと IPC 実装をそのまま使い、
// agent-app の「登録リポジトリ」と設定ファイルだけをアダプトする。
const makerIpc = require('statemachine-maker/src/main/ipc');
const store = require('../store');
const worktree = require('../worktree');

function automationConfig(config) {
  const cfg = config && typeof config === 'object' ? config : {};
  const roots = Array.isArray(cfg.repos) ? cfg.repos : [];
  return {
    roots: [...roots],
    lastRoot: roots.includes(cfg.lastRepo) ? cfg.lastRepo : (roots[0] || ''),
    skillDir: String(cfg.automationSkillDir || ''),
    agent: String(cfg.automationAgent || 'aider'),
    model: String(cfg.automationModel || ''),
  };
}

function automationPatch(config) {
  const src = config && typeof config === 'object' ? config : {};
  const patch = {};
  if (Object.prototype.hasOwnProperty.call(src, 'lastRoot')) patch.lastRepo = String(src.lastRoot || '');
  if (Object.prototype.hasOwnProperty.call(src, 'skillDir')) patch.automationSkillDir = String(src.skillDir || '');
  if (Object.prototype.hasOwnProperty.call(src, 'agent')) patch.automationAgent = String(src.agent || 'aider');
  if (Object.prototype.hasOwnProperty.call(src, 'model')) patch.automationModel = String(src.model || '');
  return patch;
}

function configAdapter() {
  return {
    load: (userData) => automationConfig(store.loadConfig(userData)),
    save: (userData, config) => automationConfig(store.saveConfig(userData, automationPatch(config))),
    addRoot: (userData, root) => automationConfig(store.addRepo(userData, root)),
    removeRoot: (userData, root) => automationConfig(store.removeRepo(userData, root)),
    isRegistered: (userData, root) => store.isRegistered(userData, root),
  };
}

function registerAutomationIpc({ getWindow, userData, appRoot }) {
  makerIpc.registerIpcHandlers(getWindow, {
    channelPrefix: 'automation:',
    config: configAdapter(),
    userData,
    appRoot,
    hooks: {
      openDelivery: async (root, delivery) => {
        const branch = String(delivery.branch || '').trim();
        const result = await worktree.create(root, {
          branch, name: worktree.slug(branch), fetchRemote: true,
        }, store.loadConfig(userData()).wslDistro);
        return { kind: 'worktree', name: result.name, branch: result.branch };
      },
    },
  });
}

module.exports = { registerAutomationIpc, automationConfig, automationPatch, configAdapter };
