'use strict';

// statemachine-maker の検証済みドメインと IPC 実装をそのまま使い、
// agent-app の「登録リポジトリ」と設定ファイルだけをアダプトする。
const os = require('os');
const path = require('path');
const makerIpc = require('./handlers');
const makerTools = require('./tools');
const store = require('../store');
const herd = require('../herd');
const worktree = require('../worktree');
const host = require('../host');
const agentCli = require('../agentCli');
const agents = require('../agents');
const settings = require('../settings');
const response = require('../response');
const { cleanAnswer } = require('../text');
const skills = require('../skills');
const skillSelection = require('../skillSelection');
const sessionSetup = require('../sessionSetup');

// タスク・ワークフローの「使う AI」の既定。設定していなければ会話の「おすすめ」（medium tier）
// と同じ CLI——会話で使えている CLI がそのままタスクでも動く（agent-herd の aider を
// 既定にしていた頃は、一族を入れていない PC で既定が常に使えなかった）。
function defaultAutomationAgent(cfg) {
  const explicit = String(cfg.automationAgent || '').trim();
  if (explicit) return explicit;
  try { return settings.resolve(cfg, { policy: 'recommended' }).cli; } catch { return ''; }
}

function automationConfig(config) {
  const cfg = config && typeof config === 'object' ? config : {};
  const roots = Array.isArray(cfg.repos) ? cfg.repos : [];
  return {
    roots: [...roots],
    lastRoot: roots.includes(cfg.lastRepo) ? cfg.lastRepo : (roots[0] || ''),
    skillDir: String(cfg.automationSkillDir || ''),
    agent: defaultAutomationAgent(cfg),
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

// タスク・ワークフローで選べる AI の名前。会話と同じ一覧（agents.js: agents/*.json の定義に
// ホストの PATH で「使える」印を付け、一族が居れば仮想の `herd` を足したもの）から、実際に
// 起こせるものだけ。agent-herd（`agent-herd defs`）には聞かない——無い PC でも選べるように。
//   distro … 省略時は登録リポジトリと設定から決める（Windows の WSL ディストロ）
async function agentDefinitions({ cwd = '', distro } = {}, { userData } = {}) {
  const resolved = distro != null ? distro
    : host.hostOf(cwd, userData ? store.loadConfig(userData()).wslDistro : '').distro;
  return agents.usableNames(await agents.listAgents(cwd, { distro: resolved }));
}

// AI 支援（読み取り専用の単発）の起動仕様。
//   herd    … agent-herd の `--purpose plan`（`--agent` は渡さず既定と宣言に任せる）
//   それ以外 … 定義（agents/*.json）から組んだ単発 argv でその CLI を直接起こす。
//              agent-herd を経由しないので、agent-tools の無い PC でも動く。
// 返す形は handlers.launchAi が読む: { command, args, input, host, outputFile, extract }
//   input      … stdin に流す本文（prompt_via: stdin の定義）
//   host       … Windows では WSL のログインシェル経由で起こす（会話と同じ側で CLI を引く）
//   outputFile … `{output_file}` を置き換えた一時ファイル（この端末の表記。終了後に読んで消す）
//   extract    … stdout（か outputFile）から回答本文を取り出す（codex の JSONL など）
function assistRunSpec({ root, agent, model = '', prompt = '' } = {}) {
  if (herd.isHerd(agent)) {
    return { ...makerTools.agentAssistRunSpec({ root, agent: '', model, prompt }), host: true };
  }
  const spec = agentCli.load(agent, root);
  const cmd = agentCli.oneShotCmd(spec, { model, readonly: true });
  let outputFile = '';
  const argv = cmd.argv.map((tok) => {
    if (!tok.includes('{output_file}')) return tok;
    if (!outputFile) outputFile = path.join(os.tmpdir(), `agent-app-assist-${process.pid}-${Date.now().toString(36)}.txt`);
    return tok.split('{output_file}').join(host.toHostPath(outputFile));
  });
  const args = argv.slice(1);
  if (cmd.promptVia === 'argv') args.push(String(prompt || ''));
  return {
    command: argv[0], args, input: cmd.promptVia === 'stdin' ? String(prompt || '') : '',
    env: cmd.env, host: true, outputFile,
    extract: (text) => response.parseTranscript(spec.name, cleanAnswer(text)).text,
    warning: cmd.readonlyWarning,
  };
}

// 選ばれた名前を agent-herd / agent-flow へ渡す名前へ写す。`herd` 以外はそのまま。用途は
// statemachine-maker が言う（task: タスクの実行 / plan: AI 支援 / flow: ワークフローの実行 /
// direct: agent-loop の無いときの手動実行）。
// task と plan は '' を返し「--agent-cli / --agent を渡さない」＝agent-herd の既定と宣言に任せる。
// direct は agent-herd に任せる口が無い（定義を名指しして直接起こす）ので、会話と同じ規則で
// 一族の共通 TUI と同じ定義（agent-herd の既定バックエンド。無ければ一族の他の定義）を返す。
async function resolveAgent({ root = '', agent = '', purpose = 'task' } = {}, { userData } = {}) {
  if (!herd.isHerd(agent)) return { agent: String(agent || '') };
  if (purpose === 'direct') {
    const distro = host.hostOf(root, userData ? store.loadConfig(userData()).wslDistro : '').distro;
    const picked = herd.resolveChat('work', await agents.listAgents(root, { distro }));
    return { agent: picked.cli, reason: picked.reason };
  }
  const picked = herd.resolveAutomation(purpose);
  return { agent: picked.agent, reason: picked.reason };
}

function automationPatch(config) {
  const src = config && typeof config === 'object' ? config : {};
  const patch = {};
  if (Object.prototype.hasOwnProperty.call(src, 'lastRoot')) patch.lastRepo = String(src.lastRoot || '');
  if (Object.prototype.hasOwnProperty.call(src, 'skillDir')) patch.automationSkillDir = String(src.skillDir || '');
  if (Object.prototype.hasOwnProperty.call(src, 'agent')) patch.automationAgent = String(src.agent || '');
  if (Object.prototype.hasOwnProperty.call(src, 'model')) patch.automationModel = String(src.model || '');
  return patch;
}

// タスク・ワークフローの実行基盤（agent-herd / agent-loop / agent-flow）は会話（tmux 対話・
// ヘッドレスの CLI 起動）と同じ経路——Windows では WSL のログインシェル経由で起こす。
// 診断・記録に使う他のコマンド（python, playwright-cli, winauto）はこの一族ではないので、
// この端末でそのまま探す（winauto は Windows の GUI 操作そのもので WSL 側には無い）。
// 一族の名前でなくても、呼ぶ側が `host: true` と言えば同じ経路に載せる——エージェント
// CLI を直接起こす AI 支援と、agent-loop の無いときにスキルの run_machine.py で回す手動実行
// （CLI が WSL に居るので、それを起こす python も WSL 側でなければならない）。
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
  return (name, { host: onHost = false } = {}) => (
    process.platform === 'win32' && (onHost || HERD_FAMILY_COMMANDS.has(name)) ? herdCommandSpawnSpec : undefined
  );
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
    agentDefinitions: (payload) => agentDefinitions(payload, { userData }),
    commandSpawnSpec: makeTaskCommandSpawnSpec(userData),
    // agent-flow が bus へ書く workspace.local（WSL の中で `git -C` に渡る clone 元）は、
    // 登録した表記のままでは向こうで開けない。ホスト（Windows なら WSL）から見た表記を渡す。
    hostPath: host.toHostPath,
    hooks: {
      resolveAgent: (payload) => resolveAgent(payload, { userData }),
      assistRunSpec,
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
  agentDefinitions, defaultAutomationAgent, assistRunSpec, resolveAgent, makeTaskCommandSpawnSpec, hostPathArgs,
};
