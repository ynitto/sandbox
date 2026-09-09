'use strict';

// agent-loop の無い PC での手動実行。同梱の statemachine-use スキル（run_machine.py）の
// exec バックエンドに、agents/*.json から組んだ単発 argv を渡して工程ごとに CLI を起こす。
//
// agent-loop（agentcore の harness）があるときはそちらが正典で、履歴・定期実行・台帳も
// 持つ。ここは「無くてもタスクの本体は回る」ための最小経路で、定義・工程・遷移の読み方は
// 同じスキルのスクリプトなので、どちらで回しても定義の意味は変わらない。
//
// Windows では CLI が WSL に居るので、それを起こす python も WSL 側で動かす（host: true。
// スキルの置き場は WSL から見える表記へ直す）。他の OS はこの端末の python。

const path = require('path');
const agentCli = require('../agentCli');
const agentLoop = require('./agent-loop');

const WSL_PYTHON = 'python3';

function machineWorkflow(machine) {
  return `.statemachine/${agentLoop.machineName(machine)}/workflow.yaml`;
}

// { command, args, host, warning } を返す。
//   root        … 登録リポジトリ（cwd）
//   machine     … タスクの保存名
//   agent       … 定義の名前（agents/*.json。`herd` は呼ぶ側で写してから渡す）
//   skillDir    … statemachine-use スキルの置き場（この端末の表記）
//   python      … この端末で使う python の綴り（Windows では使わず WSL の python3）
//   hostPath    … ホスト（Windows なら WSL）から見た表記へ直す関数
function runSpec({
  root, machine, agent, model = '', parameters = {}, instruction = '',
  skillDir, python = 'python3', hostPath = (v) => String(v || ''), platform = process.platform,
} = {}) {
  if (!skillDir) throw new Error('statemachine-use スキルのスクリプトが見つかりません（「実行環境」を確認してください）');
  if (!agent) throw new Error('使う AI を選んでください');
  const spec = agentCli.load(agent, root);
  const cmd = agentCli.oneShotCmd(spec, { model, readonly: false });
  const onHost = platform === 'win32';
  const script = path.join(skillDir, 'scripts', 'run_machine.py');
  const args = [
    onHost ? hostPath(script) : script,
    machineWorkflow(machine),
    '--agent', 'exec',
    '--agent-command', JSON.stringify(cmd.argv),
    '--prompt-via', cmd.promptVia,
    '--result-line',
  ];
  if (instruction) args.push('--instruction', String(instruction));
  const params = parameters && typeof parameters === 'object' ? parameters : {};
  for (const key of Object.keys(params).sort()) {
    const value = params[key];
    if (value == null) continue;
    if (key === 'input') args.push('--input', String(value));
    else args.push('--context', `${key}=${value}`);
  }
  return { command: onHost ? WSL_PYTHON : python, args, host: onHost, env: cmd.env, warning: cmd.readonlyWarning };
}

module.exports = { runSpec, machineWorkflow, WSL_PYTHON };
