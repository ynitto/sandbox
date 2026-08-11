'use strict';

// 定型業務の Aider 実行が「Windows のダッシュボード → WSL の agent-loop statemachine」
// へ正しく渡ることの検証。旧 in-process 実行器（stateMachineRunner.js）のテストを
// 置き換える。実行境界が WSL 側へ移ったので、見るのは主に次の 3 点。
//   1. ハーネスの起動引数（cwd 相対の workflow・モデル・パラメータ）
//   2. win32 で wsl.exe を必ず経由すること（Windows 側で直接 spawn しない）
//   3. tmux セッションの作り方（一回限りの実行を二重に走らせない・結果を残す）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const wslMain = require('../src/base/main/wsl');

// win32 の起動は「窓を開く前に wsl.exe を実地検査する」。テスト機に WSL は無いので
// 検査だけ差し替え、起動コマンドの組み立てとスクリプト本体を見る（cowork.test.js と同じ）。
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

const cowork = require('../src/features/cowork/main/cowork');
const {
  commandWindowScript, runCommandWindow, runCommandCapture, cliSpawnSpec,
} = require('../src/features/cowork/main/loopProvider');

function onWin32(fn) {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    return fn();
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
}

let passed = 0;
function test(name, fn) {
  const done = fn();
  const finish = () => { passed += 1; console.log(`ok - ${name}`); };
  return done && typeof done.then === 'function' ? done.then(finish) : Promise.resolve(finish());
}

async function main() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-window-'));

  await test('ハーネス起動引数は cwd 相対の workflow・モデル・パラメータを渡す', () => {
    const args = cowork.stateMachineHarnessArgs(
      repo, path.join(repo, '.statemachine', 'digest', 'workflow.yaml'),
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
    ]);
    const noModel = cowork.stateMachineHarnessArgs(repo, path.join(repo, 'w.yaml'), { cli: 'aider' }, {}, {});
    assert.ok(!noModel.includes('--model'),
      'モデル未指定なら --model を付けない（CLI 定義の default_model に任せる）');
  });

  await test('workflow は必ず作業フォルダ内の相対パス（外を指す組み合わせは起動前に断る）', () => {
    // ハーネスは WSL 側で動くので、ビュアーの絶対パスをそのまま渡すと解決できない。
    // win32 の path.relative は道筋が無いと絶対パスを返すため、ここで気付けないと
    // 「作業フォルダ外」だけが WSL 側から返ってきて原因が分からなくなる。
    assert.throws(
      () => cowork.harnessWorkflowArg(repo, path.join(os.tmpdir(), 'other', 'workflow.yaml'), {}),
      /作業フォルダの外/
    );
    assert.strictEqual(
      cowork.harnessWorkflowArg(repo, path.join(repo, 'sub', '.statemachine', 'x', 'workflow.yaml'), {}),
      'sub/.statemachine/x/workflow.yaml',
      'サブフォルダの定義は POSIX 相対で渡す'
    );
  });

  await test('win32 の非ウィンドウ実行は wsl.exe 経由（agent-loop を Windows 側で直接 spawn しない）', async () => {
    // agent-loop は WSL 側にしか無い。ここを直接 spawn にすると ENOENT で必ず失敗する。
    const spec = onWin32(() => cliSpawnSpec('agent-loop', ['statemachine', '--workflow', 'w.yaml'], 'C:\\proj\\app'));
    assert.strictEqual(spec.command, 'wsl.exe');
    const script = spec.args[spec.args.length - 1];
    assert.match(script, /cd '\/mnt\/c\/proj\/app' &&/, 'cwd は WSL の Linux パスへ翻訳して cd する');
    assert.match(script, /'agent-loop' 'statemachine' '--workflow' 'w\.yaml'/);
    assert.ok(!('cwd' in spec.options), 'Windows 側に無いパスを spawn の cwd へ渡さない');

    const uncSpec = onWin32(() => cliSpawnSpec('agent-loop', [], '\\\\wsl$\\Ubuntu\\home\\me\\repo'));
    assert.deepStrictEqual(uncSpec.args.slice(0, 2), ['-d', 'Ubuntu'], 'UNC からディストロを指定する');

    const res = await onWin32(() => runCommandCapture('agent-loop', ['statemachine'], { cwd: 'C:\\proj\\app' }));
    // Linux 上のテストでは wsl.exe が無く ENOENT になるが、その ENOENT が agent-loop
    // ではなく wsl.exe を指していること＝WSL 経由であることを検証する。
    assert.strictEqual(res.ok, false);
    assert.match(res.error, /wsl\.exe/, res.error);
    assert.ok(!/spawn agent-loop/.test(res.error), 'agent-loop を Windows 側で直接 spawn しない');
  });

  await test('win32 のウィンドウ実行は cmd start → wsl.exe → bash で WSL パスの tmux を開く', () => {
    const launched = onWin32(() => runCommandWindow({
      command: 'agent-loop',
      args: ['statemachine', '--workflow', '.statemachine/digest/workflow.yaml', '--model', 'gemma4:e4b'],
      cwd: 'C:\\proj\\app',
      sessionKey: 'aider',
      title: '定型業務を実行',
    }));
    assert.strictEqual(launched.ok, true, launched.error);
    assert.match(launched.windowCommand, /^cmd\.exe \/d \/c start /, 'cmd の start でウィンドウを開く');
    assert.match(launched.windowCommand, /wsl\.exe .*-e bash -lc /, 'wsl.exe で bash ログインシェルを起動する');
    const body = fs.readFileSync(launched.scriptFile, 'utf8');
    assert.match(body, /cd '\/mnt\/c\/proj\/app'/, 'cwd は WSL の Linux パスへ翻訳する');
    assert.match(body, /tmux new-session -d -s "\$__ses" -c '\/mnt\/c\/proj\/app'/,
      'tmux セッションも WSL パスで開く');
    assert.ok(body.includes("'--model' 'gemma4:e4b'"), 'モデル指定が argv に残る');
    assert.match(launched.session, /^agent-sm-aider-[0-9a-f]{8}-[a-z0-9]+$/,
      '実行ごとに一意なセッション名（常駐チャットへ合流させない）');
  });

  await test('tmux は結果を取りこぼさず、一回限りの実行を二重に走らせない', () => {
    const script = commandWindowScript({
      command: 'agent-loop',
      args: ['statemachine', '--workflow', 'w.yaml'],
      cwd: '/home/me/repo',
      session: 'agent-sm-aider-abc123-x1',
    });
    // 実行コマンドはスクリプト中にちょうど 1 回だけ現れる。チャット経路は起動失敗時に
    // 同じ CLI を窓で再実行して原因を見せるが、ステートマシンでそれをやるとファイル
    // 編集ごと二重に走る。
    assert.strictEqual(script.split("'agent-loop' 'statemachine'").length - 1, 1,
      '同じコマンドを二度書かない（＝二重実行の経路が無い）');
    // 再実行の代わりに、起動できない唯一の実質的な原因を事前に確かめる
    // （tmux は exec に失敗しても何も残さず pane ごと消える）。
    assert.match(script, /command -v 'agent-loop' >\/dev\/null 2>&1 \|\|/);
    assert.match(script, /PATH に見つかりません/);
    // placeholder のうちに pipe-pane を繋ぎ、respawn で本命へ差し替える。
    // 実行後にアタッチすると pane の内容はリサイズで消えるため、記録はファイルに採る。
    assert.match(script, /tmux new-session -d -s "\$__ses" -c '\/home\/me\/repo' sleep 300/);
    assert.match(script, /tmux pipe-pane -o -t "\$__ses" "cat >> '\$__out'"/);
    assert.match(script, /tmux respawn-pane -k -t "\$__ses" -c '\/home\/me\/repo' 'agent-loop' 'statemachine'/);
    assert.ok(script.indexOf('pipe-pane') < script.indexOf('respawn-pane'),
      'pipe-pane は本命の起動前に繋ぐ（最初の行を取りこぼさない）');
    // 続いているなら再接続の方法を、終わっているなら記録の末尾を窓へ出す。
    assert.match(script, /実行は継続中です。再接続: tmux attach -t \$__ses/);
    assert.match(script, /実行が終了しました。出力（末尾）:[\s\S]*tail -n 30 "\$__out"/);
  });

  await test('ウィンドウ非対応環境は RESULT 行を実行結果の契約として読む', async () => {
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
  });

  console.log(`\n${passed} tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
