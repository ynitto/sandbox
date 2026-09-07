'use strict';

// statemachine-maker の検証済みドメインと IPC 実装をそのまま使い、
// agent-app の「登録リポジトリ」と設定ファイルだけをアダプトする。
const makerIpc = require('statemachine-maker/src/main/ipc');
const makerTools = require('statemachine-maker/src/main/tools');
const store = require('../store');
const herd = require('../herd');
const worktree = require('../worktree');
const host = require('../host');
const agentCli = require('../agentCli');
const skills = require('../skills');
const skillSelection = require('../skillSelection');
const sessionSetup = require('../sessionSetup');

function automationConfig(config) {
  const cfg = config && typeof config === 'object' ? config : {};
  const roots = Array.isArray(cfg.repos) ? cfg.repos : [];
  return {
    roots: [...roots],
    lastRoot: roots.includes(cfg.lastRepo) ? cfg.lastRepo : (roots[0] || ''),
    skillDir: String(cfg.automationSkillDir || ''),
    agent: String(cfg.automationAgent || 'aider'),
    model: String(cfg.automationModel || ''),
    instructions: cfg.instructions && typeof cfg.instructions === 'object' ? { ...cfg.instructions } : {},
    execution: cfg.execution && typeof cfg.execution === 'object' ? { ...cfg.execution } : {},
  };
}

async function prepareRun(userData, { root, task, agent, parameters, skillMode, selectedSkills }) {
  const cfg = store.loadConfig(userData());
  const spec = agentCli.load(agent, root);
  const plan = sessionSetup.planActions(cfg.instructions.startupActions, {
    ...spec, availableSkills: skills.list(root),
  });
  const target = host.hostOf(root, cfg.wslDistro);
  const startup = await sessionSetup.runCommands(plan.commands, (command, timeoutMs) => (
    target.shell.run(`cd ${host.sq(target.cwd)} && ${command}`, { timeoutMs })
  ));
  const selectionConfig = cfg.instructions.skillSelection || {};
  const resolved = skillSelection.select({
    mode: skillMode || selectionConfig.defaultMode || 'auto',
    text: JSON.stringify({ task, parameters: parameters || {} }),
    requested: selectedSkills,
    candidates: selectionConfig.enabled === false ? [] : selectionConfig.candidates,
    catalog: skills.catalog(root),
  });
  const delivery = skillSelection.deliver(resolved, spec);
  const skillInstruction = plan.skills.length
    ? `開始時に次のスキルを適用してください:\n${plan.skills.map((item) => item.command).join('\n')}`
    : '';
  const selectedInstruction = delivery.commands.length
    ? `今回の実行で次のスキルを適用してください:\n${delivery.commands.join('\n')}`
    : delivery.instruction;
  return {
    instruction: [sessionSetup.instructionBlock(cfg.instructions), skillInstruction, selectedInstruction].filter(Boolean).join('\n\n'),
    warning: [plan.warning, startup.warning].filter(Boolean).join('\n'),
    information: [...startup.information, ...delivery.information],
    skillSelection: resolved,
  };
}

function selectForRequest(userData, { root, text, mode, selected }) {
  if (!store.isRegistered(userData(), root)) throw new Error('登録していないフォルダです');
  const cfg = store.loadConfig(userData());
  const selection = cfg.instructions.skillSelection || {};
  const result = skillSelection.select({
    mode: mode || selection.defaultMode || 'auto', text, requested: selected,
    candidates: selection.enabled === false ? [] : selection.candidates,
    catalog: skills.catalog(root),
  });
  return { ...result, selected: result.selected.map(({ content, path, ...item }) => item) };
}

// タスク・ワークフローで選べる AI の名前。agent-herd が解決できる定義（実際に起こせるもの）に、
// 一族が居れば仮想の `herd` を足す。定義の有無は agent-app の探索で見る（一覧の形が要るため）。
async function agentDefinitions({ cwd = '', capture } = {}) {
  const names = await makerTools.agentDefinitions({ cwd, capture });
  return herd.withVirtualName(names, agentCli.list(cwd));
}

// 選ばれた名前を実際に起こす定義へ写す。`herd` 以外はそのまま。用途は statemachine-maker が
// 言う（task: タスク・ワークフローの実行 / plan: AI 支援）。一族の一員が起こせるかは
// ここでは見ない——`herd` が一覧に載るのは agent-herd がその一員を解決できたときだけで
// （agentDefinitions）、ollama サーバの有無などは agent-herd 自身が起動時に言う。
function resolveAgent({ root = '', agent = '', purpose = 'task' } = {}) {
  if (!herd.isHerd(agent)) return { agent: String(agent || '') };
  const picked = herd.resolve(purpose, agentCli.list(root).map((d) => ({ ...d, available: true })));
  return { agent: picked.cli, purpose: picked.purpose, reason: picked.reason };
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
    agentDefinitions,
    hooks: {
      resolveAgent,
      prepareRun: (payload) => prepareRun(userData, payload),
      selectSkills: (payload) => selectForRequest(userData, payload),
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

module.exports = { registerAutomationIpc, automationConfig, automationPatch, configAdapter, prepareRun, selectForRequest, agentDefinitions, resolveAgent };
