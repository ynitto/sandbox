'use strict';

// クラウドの CLI を **1 セッション**で通す実行経路。
//
// 工程ごとにヘッドレス起動すると、CLI の起動・文脈の再構築・システムプロンプトの再送が
// 工程の数だけ掛かる（クラウドではトークン消費が跳ねる）。あちらは自分でスキルを見つけて
// 1 セッションで通せるので、送るのは発動文 1 つだけにする。ローカルの CLI は従来どおり
// 工程ごとのハーネス（agent-loop / run_machine.py）で回す——検査・証跡・履歴はそちらにある。
//
// **経路の分かれ目は定義の宣言 1 つ**（`agents/*.json` の `relative_cost`。ローカル=0 /
// 通常のクラウド=1）。名前の許可リストは持たない。同じ分け方を agent-loop のデーモン
// （scheduler の対話ペイン経路）と agent-dashboard も持つ。
//
// 送る文面の綴りは `agentcore.loopentry.statemachine_command`（`slash=False`）と同じにする。
// ここで別の言い方をすると「画面から回すと AI の受け取り方が違う」が生まれる。
//
// **この経路が持たないもの**: 工程ごとの検査（`check`）・成果物の証跡確認・工程数の上限・
// 実行履歴。成否は終了コードで見る（`RESULT` 行は出ない）。

const os = require('os');
const path = require('path');
const agentCli = require('../agentCli');
const agentLoop = require('./agent-loop');

// この定義を 1 セッションで通してよいか。問いは 2 つだけで、どちらも定義の宣言である。
//   1. クラウドか（`relative_cost` > 0）… ローカルは工程ごとに起こしても費用が増えない
//      ので、検査と証跡のあるハーネスのままが得。
//   2. 自分でツールを回せるか（`headless_autonomy: tool-loop`）… 回せない定義へ発動文を
//      送っても、返るのは 1 回の答えであって実行ではない。
// 読めない定義は false（従来どおり工程ごとのハーネス）。分からないときは切り替えない。
function runsInOneSession(agent, root, model = '') {
  const name = String(agent || '').trim();
  if (!name) return false;
  let spec;
  try {
    spec = agentCli.load(name, root);
  } catch {
    return false;
  }
  return !agentCli.isLocal(spec, model) && spec.headlessAutonomy === 'tool-loop';
}

// CLI へ送る 1 行（＋条件）。綴りは agentcore.loopentry.statemachine_command と同じ。
function invocation({ machine, parameters = {}, instruction = '' }) {
  const name = agentLoop.machineName(machine);
  const values = parameters && typeof parameters === 'object' ? parameters : {};
  const conditions = Object.keys(values).sort()
    .filter((key) => values[key] != null && String(values[key]).trim())
    .map((key) => `\n- ${key}: ${values[key]}`)
    .join('');
  const body = `statemachine-use スキルで${name}ステートマシンを実行して`
    + (conditions ? `\n\n入力:${conditions}` : '');
  const common = String(instruction || '').trim();
  return common ? `${common}\n\n${body}` : body;
}

// { command, args, input, host, env, outputFile, prompt } を返す。
//   agent … 定義の名前（`herd` のような仮想名は呼ぶ側で実体へ写してから渡す）
function runSpec({ root, machine, agent, model = '', parameters = {}, instruction = '' } = {}) {
  if (!agent) throw new Error('使う AI を選んでください');
  const spec = agentCli.load(agent, root);
  const cmd = agentCli.oneShotCmd(spec, { model, readonly: false });
  const prompt = invocation({ machine, parameters, instruction });
  let outputFile = '';
  const argv = cmd.argv.map((token) => {
    if (!token.includes('{output_file}')) return token;
    if (!outputFile) {
      outputFile = path.join(os.tmpdir(), `agent-app-run-${process.pid}-${Date.now().toString(36)}.txt`);
    }
    return token.split('{output_file}').join(outputFile);
  });
  const args = argv.slice(1);
  if (cmd.promptVia === 'argv') args.push(prompt);
  return {
    command: argv[0], args, prompt, outputFile,
    input: cmd.promptVia === 'stdin' ? prompt : '',
    // 会話・AI 支援と同じく、CLI 本体は Windows では WSL 側に居る。
    host: true, env: cmd.env,
    warning: cmd.readonlyWarning,
  };
}

module.exports = { runsInOneSession, invocation, runSpec };
