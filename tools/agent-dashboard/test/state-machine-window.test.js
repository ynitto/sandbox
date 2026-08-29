'use strict';

// 対話ペインを持たない CLI の実行が「Windows のダッシュボード → WSL の agent-loop
// （定型業務は `statemachine`・定期プロンプトは `run`）」へ正しく渡ることの検証。
// 旧 in-process 実行器（stateMachineRunner.js）のテストを置き換える。
// 実行境界が WSL 側へ移ったので、見るのは主に次の 3 点。
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

  await test('定期プロンプトも対話ペインの有無で経路を分けず、headless CLI は agent-loop run へ渡す', async () => {
    // tmux は「コマンドを送る手段・結果を見る手段」で、対話 CLI 専用の仕組みではない。
    // 対話節を持たない CLI（aider）が選ばれても実行を断らず、同じウィンドウ経路で
    // `agent-loop run`（単発実行）を起こす。受入条件は設定から解決して渡す
    // ——ツールループ非内蔵の CLI では、これが無いと done を機械検証できない。
    const loopRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-headless-loop-'));
    fs.mkdirSync(path.join(loopRepo, '.agents'));
    fs.writeFileSync(path.join(loopRepo, '.agents', 'agent-loop.yaml'), [
      'prompts:',
      '  - name: ログ要約',
      '    prompt: ログを要約して `reports/digest.md` に書いて',
      '    interval_minutes: 600',
      '    acceptance:',
      '      - "`reports/digest.md` が更新されている"',
      '      - 件数が書かれている',
      '',
    ].join('\n'));
    const controlDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-headless-ctl-'));
    fs.writeFileSync(path.join(controlDir, 'control.json'), `${JSON.stringify({
      version: 1, revision: 1,
      workloads: { routine: { agent_cli: 'aider', model: 'ollama/gemma4:e4b' } },
    })}\n`);
    const config = {
      orchestration: { controlDir },
      cowork: {
        loopCommand: 'echo',
        runWindow: false,   // 引数の組み立てを見る（窓を開く経路は上のテスト）
        items: [{ id: 'daily', type: 'loop', name: 'ログ要約', repo: loopRepo }],
      },
    };
    assert.deepStrictEqual(
      cowork.resolveLoopAcceptance(loopRepo, 'ログ要約', config),
      ['`reports/digest.md` が更新されている', '件数が書かれている']
    );
    // 1 件だけならスカラーでも書ける（agent-loop の validate_entries と同じ受け方）。
    // ここを配列限定にしていると、書いてあるのに「受入条件が無い」と言われる。
    const scalarRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-headless-scalar-'));
    fs.mkdirSync(path.join(scalarRepo, '.agents'));
    fs.writeFileSync(path.join(scalarRepo, '.agents', 'agent-loop.yaml'), [
      'prompts:',
      '  - name: ログ要約',
      '    prompt: ログを要約して',
      '    acceptance: 要約ファイルが作成されていること',
      '    interval_minutes: 600',
      '',
    ].join('\n'));
    assert.deepStrictEqual(cowork.resolveLoopAcceptance(scalarRepo, 'ログ要約', config),
                           ['要約ファイルが作成されていること']);
    const res = await cowork.runLoop(config, 'daily');
    assert.strictEqual(res.ok, true, res.error || res.stderr);
    const argv = res.stdout.split(' ');
    assert.strictEqual(argv[0], 'run', '単発実行サブコマンドへ渡す（send ではない）');
    assert.ok(res.stdout.includes('--agent-cli aider'), '段で解決した CLI を明示する');
    assert.ok(res.stdout.includes('--model ollama/gemma4:e4b'), 'モデルもその回だけ明示する');
    assert.strictEqual((res.stdout.match(/--acceptance/g) || []).length, 2,
      '受入条件は設定から解決して 1 件ずつ渡す');
  });

  await test('定型業務で ollama が選ばれたら、スキル発動文ではなくハーネスへ渡す', async () => {
    // 2026-08-29 の回帰: 実行レベル「単純作業」（ollama / gemma4:e4b）で今すぐ実行すると、
    // 共通 TUI のペインへ「statemachine-use スキルで…」が送られ、モデルが
    // 「実行できません」と答えて終わっていた。TUI のコマンド語彙は `/sm` で、自然文の
    // 起動文からスキルを解決する仕組みは持たない。
    const smRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-herd-'));
    fs.mkdirSync(path.join(smRepo, '.statemachine', 'digest'), { recursive: true });
    fs.writeFileSync(path.join(smRepo, '.statemachine', 'digest', 'workflow.yaml'), [
      'name: ダイジェスト',
      'initial_state: start',
      'states:',
      '  start:',
      '    action: 要約する',
      '    terminal: true',
      'transitions: []',
      '',
    ].join('\n'));
    const controlDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-sm-herd-ctl-'));
    fs.writeFileSync(path.join(controlDir, 'control.json'), `${JSON.stringify({
      version: 1, revision: 1,
      workloads: { routine: { agent_cli: 'ollama', model: 'gemma4:e4b' } },
    })}\n`);
    const config = {
      orchestration: { controlDir },
      cowork: {
        loopCommand: 'echo',
        runWindow: false,   // 引数の組み立てを見る（窓を開く経路は上のテスト）
        items: [{ id: 'sm1', type: 'state-machine', name: 'ダイジェスト', workflow: 'digest', repo: smRepo }],
      },
    };
    const res = await cowork.runStateMachine(config, 'sm1', {});
    assert.strictEqual(res.ok, true, res.error || res.stderr);
    assert.strictEqual(res.stdout.trim().split(' ')[0], 'statemachine',
      'ハーネスのサブコマンドへ渡す（send ではない）');
    assert.ok(res.stdout.includes('--workflow .statemachine/digest/workflow.yaml'));
    assert.ok(res.stdout.includes('--agent-cli ollama'), '段で解決した CLI を明示する');
    assert.ok(res.stdout.includes('--model gemma4:e4b'), 'モデルもその回だけ明示する');
    assert.ok(!res.stdout.includes('スキル'), 'ペインへ送る発動文は組み立てない');
  });

  test('ハーネスへ回すかは headless_autonomy で決める（interactive の有無で代理しない）', () => {
    // 対話面を提供するか（interactive）と、自分で探索・実行まで回せるか（headless_autonomy）は
    // 別の宣言である。両者を同じフラグで表すと、aider のように「対話もできるが
    // ヘッドレスでは single-shot」の CLI を取り違え、定型業務が黙ってハーネスから
    // 対話送信へ切り替わる（2026-08-25 に実際に踏んだ）。
    const f = cowork.needsHeadlessHarness;
    // 対話面があっても single-shot ならハーネス（aider）
    assert.strictEqual(f({ interactive: { command: ['x'] }, headlessAutonomy: 'single-shot' }), true,
      'single-shot は対話面があってもハーネスで回す');
    // 対話面があって tool-loop ならペイン（kiro / claude / ollama）
    assert.strictEqual(f({ interactive: { command: ['x'] }, headlessAutonomy: 'tool-loop' }), false,
      'tool-loop は対話ペインで駆動できる');
    // 対話面が無ければ、駆動しようが無いのでハーネス（tool-loop でも）
    assert.strictEqual(f({ headlessAutonomy: 'tool-loop' }), true,
      'interactive を持たない CLI はペインで駆動できない');
    assert.strictEqual(f({ headlessAutonomy: 'single-shot' }), true);
    // 未宣言は安全側（＝従来どおりハーネス）
    assert.strictEqual(f({ interactive: { command: ['x'] } }), true, '未宣言は single-shot 扱い');
    assert.strictEqual(f(null), true, 'spec が無ければハーネス');
  });

  test('定型業務は agent-herd 一族もハーネスへ回す（tool-loop の申告でも）', () => {
    // 一族の対話面は共通 TUI で、コマンド語彙は agentcore のルート表（`/sm`）である。
    // ここをペインへ倒すと、この画面が送る起動文「statemachine-use スキルで…」が本文と
    // して推論へ流れ、モデルが「実行できません」と答えて終わる（2026-08-29 に踏んだ）。
    const { loadCli } = require('../src/features/agent-project/main/agentCli.js');
    const root = path.join(__dirname, '..', '..', '..');
    const ollama = loadCli('ollama', root);
    assert.strictEqual(ollama.headlessAutonomy, 'tool-loop', 'ヘッドレスは自分でツールを回す');
    assert.ok(ollama.interactive, 'ollama.json に interactive がある（CLIチャットのため）');
    assert.strictEqual(cowork.isHerdFamily(ollama), true, 'command[0] が agent-herd なら一族');
    assert.strictEqual(cowork.needsStateMachineHarness(ollama), true,
      '一族の定型業務は対話送信ではなくハーネスで回す');
    // 送る本文が違うので、定期プロンプト・アドホックの経路は変えない（人が書いた指示文は
    // どの CLI でも意味を成す）。
    assert.strictEqual(cowork.needsHeadlessHarness(ollama), false,
      '定期プロンプトは従来どおり対話ペインで起こす');
    // クラウド CLI は巻き込まない（自然文からスキルを見つけて自分で回せる）。
    const claude = loadCli('claude', root);
    assert.strictEqual(cowork.isHerdFamily(claude), false);
    assert.strictEqual(cowork.needsStateMachineHarness(claude), false);
    // 綴りだけで判定する（宣言に family フィールドを足さない）。
    assert.strictEqual(cowork.isHerdFamily({ command: ['agent-herd', 'ollama'] }), true);
    assert.strictEqual(cowork.isHerdFamily({ command: ['claude'], interactive: { command: ['agent-herd', 'x'] } }), true);
    assert.strictEqual(cowork.isHerdFamily({ command: ['kiro-cli'] }), false);
  });

  test('aider は interactive を持っていてもハーネス経路のまま（回帰の固定）', () => {
    // aider.json に interactive を足したとき、定型業務の実行経路が変わっていないこと。
    const { loadCli } = require('../src/features/agent-project/main/agentCli.js');
    const spec = loadCli('aider', path.join(__dirname, '..', '..', '..'));
    assert.ok(spec.interactive, 'aider.json に interactive がある（chat aider のため）');
    assert.strictEqual(spec.headlessAutonomy, 'single-shot');
    assert.strictEqual(cowork.needsHeadlessHarness(spec), true,
      'aider の定型業務は対話送信ではなくハーネスで回す');
  });

  console.log(`\n${passed} tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
