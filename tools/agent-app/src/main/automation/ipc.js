'use strict';

// statemachine-maker の検証済みドメインと IPC 実装をそのまま使い、
// agent-app の「登録リポジトリ」と設定ファイルだけをアダプトする。
const makerIpc = require('./handlers');
const makerTools = require('./tools');
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
  // agent が ''（herd。--agent-cli を渡さない）なら、スキルの渡し方は harness の既定の定義で決める
  const spec = agentCli.load(agent || herd.HARNESS_DEFAULT, root);
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

// 選ばれた名前を agent-herd / agent-flow へ渡す名前へ写す。`herd` 以外はそのまま。用途は
// statemachine-maker が言う（task: タスクの実行 / plan: AI 支援 / flow: ワークフローの実行）。
// task と plan は '' を返し「--agent-cli / --agent を渡さない」＝agent-herd の既定と宣言に任せる。
function resolveAgent({ agent = '', purpose = 'task' } = {}) {
  if (!herd.isHerd(agent)) return { agent: String(agent || '') };
  const picked = herd.resolveAutomation(purpose);
  return { agent: picked.agent, reason: picked.reason };
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

// タスク・ワークフローの実行基盤（agent-herd / agent-loop / agent-flow）は会話（tmux 対話・
// ヘッドレスの CLI 起動）と同じ経路——Windows では WSL のログインシェル経由で起こす。
// 診断・記録に使う他のコマンド（python, playwright-cli, winauto）はこの一族ではないので、
// この端末でそのまま探す（winauto は Windows の GUI 操作そのもので WSL 側には無い）。
const HERD_FAMILY_COMMANDS = new Set(['agent-herd', 'agent-loop', 'agent-flow']);

// 値がホスト側のパスになるオプション。WSL のログインシェルへ載せ替えるときは、cwd と
// 同じようにここも WSL 表記へ直してから渡す——直さないと、WSL の中の agent-loop が
// `C:\…` という名前の相対パスを掴む。
//   --dir … agent-loop / agent-herd の作業対象（登録したリポジトリ）
//   --bus … agent-flow の共有 bus（Windows 側の Node も同じ実体を fs で読むので、
//           /mnt/c/… へ直せば両側が同じ場所を指す）
// ほかの引数は相対パス（--workflow）か自由文（-p / run の依頼文 / --reason）なので触らない。
// パスを渡すオプションを増やしたら、ここへ足す。
const HOST_PATH_OPTIONS = new Set(['--dir', '--bus']);

function hostPathArgs(args) {
  const list = (Array.isArray(args) ? args : []).map((item) => String(item));
  return list.map((item, index) => (index > 0 && HOST_PATH_OPTIONS.has(list[index - 1]) ? host.toWslPath(item) : item));
}

function makeTaskCommandSpawnSpec(userData) {
  function herdCommandSpawnSpec(command, args, { cwd = '' } = {}) {
    const distro = host.hostOf(cwd, store.loadConfig(userData()).wslDistro).distro;
    const wsl = host.wslArgv(command, hostPathArgs(args), { cwd, distro });
    return { command: wsl.command, args: wsl.args, options: { windowsHide: true } };
  }
  return (name) => (process.platform === 'win32' && HERD_FAMILY_COMMANDS.has(name) ? herdCommandSpawnSpec : undefined);
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
    commandSpawnSpec: makeTaskCommandSpawnSpec(userData),
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

module.exports = {
  registerAutomationIpc, automationConfig, automationPatch, configAdapter, prepareRun, selectForRequest,
  agentDefinitions, resolveAgent, makeTaskCommandSpawnSpec, hostPathArgs,
};
