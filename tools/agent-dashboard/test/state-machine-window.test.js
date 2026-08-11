'use strict';

// 定型業務の Aider 実行が tmux ウィンドウ経由の agent-loop statemachine ハーネスへ
// 渡ること（起動引数・ウィンドウスクリプト・非ウィンドウ時の結果契約）の検証。
// 旧 in-process 実行器（stateMachineRunner.js）のテストを置き換える。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const cowork = require('../src/features/cowork/main/cowork');
const {
  commandWindowScript, runCommandCapture,
} = require('../src/features/cowork/main/loopProvider');

async function main() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-window-'));
  const workflowPath = path.join(repo, '.statemachine', 'digest', 'workflow.yaml');

  // 1) ハーネス起動引数: ワークフローは cwd 相対 POSIX、モデルとパラメータを明示する
  const args = cowork.stateMachineHarnessArgs(
    repo, workflowPath,
    { cli: 'aider', model: 'gemma4:e4b' },
    { topic: 'llm', input: '今日の要約' },
    {}
  );
  assert.deepStrictEqual(args, [
    'statemachine',
    '--workflow', '.statemachine/digest/workflow.yaml',
    '--agent-cli', 'aider',
    '--model', 'gemma4:e4b',
    '--param', 'topic=llm',
    '--param', 'input=今日の要約',
  ], 'ワークフロー相対パス・--model・--param を組み立てる');

  const noModel = cowork.stateMachineHarnessArgs(repo, workflowPath, { cli: 'aider' }, {}, {});
  assert.ok(!noModel.includes('--model'), 'モデル未指定なら --model を付けない（定義の default_model に任せる）');

  // 2) ウィンドウスクリプト: 一意セッションの tmux new-session と生存チェックがある
  const script = commandWindowScript({
    command: 'agent-loop',
    args: [...args, '--hold'],
    cwd: '/home/user/repo',
    session: 'agent-sm-aider-abc123-x1',
  });
  assert.match(script, /tmux new-session -d -s "\$__ses" -c '\/home\/user\/repo' 'agent-loop' 'statemachine'/,
    'tmux セッションでハーネスを起動する');
  assert.ok(script.includes("'--model' 'gemma4:e4b'"), 'モデル指定が argv に残る');
  assert.ok(script.includes("'--hold'"), 'ウィンドウ保持フラグを渡す');
  assert.ok(script.includes('tmux has-session'), '起動直後の生存チェックがある');
  assert.ok(script.includes('tmux attach'), '実行の様子を見せるためにアタッチする');

  // 3) 非ウィンドウ環境のフォールバック: RESULT 行を結果契約として解析する
  const okRes = await runCommandCapture(process.execPath, [
    '-e', 'console.log("noise"); console.log(\'RESULT {"ok":true,"stdout":"OK","finalState":"complete"}\')',
  ], { cwd: repo, timeoutMs: 20000 });
  assert.strictEqual(okRes.ok, true, okRes.error);
  assert.strictEqual(okRes.finalState, 'complete');
  assert.strictEqual(okRes.stdout, 'OK');

  const failRes = await runCommandCapture(process.execPath, [
    '-e', 'console.log(\'RESULT {"ok":false,"error":"ステート make が Output Contract を満たしませんでした"}\'); process.exit(1)',
  ], { cwd: repo, timeoutMs: 20000 });
  assert.strictEqual(failRes.ok, false);
  assert.match(failRes.error, /Output Contract/);

  const plainRes = await runCommandCapture(process.execPath, ['-e', 'console.log("done")'],
    { cwd: repo, timeoutMs: 20000 });
  assert.strictEqual(plainRes.ok, true, 'RESULT 行が無ければ exit code で判定する');
  assert.strictEqual(plainRes.stdout, 'done');

  const missing = await runCommandCapture(path.join(repo, 'no-such-command'), [], { cwd: repo });
  assert.strictEqual(missing.ok, false, '起動失敗はエラーとして返す');
  assert.ok(missing.error, missing.error);

  console.log('ok - Aider 定型業務は tmux ウィンドウの agent-loop statemachine ハーネスで実行される');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
