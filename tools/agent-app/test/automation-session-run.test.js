'use strict';

// 実行の経路を分ける規則と、クラウドの CLI へ送る 1 行を縛る。
// 分かれ目は定義の宣言だけで、CLI 名の許可リストは持たない。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const sessionRun = require('../src/main/automation/session-run');
const agentCli = require('../src/main/agentCli');

const REPO = path.join(__dirname, '..', '..', '..');

test('経路は定義の宣言で決まる: クラウドかつ自分でツールを回せる定義だけ 1 セッション', () => {
  for (const name of ['kiro', 'claude', 'codex', 'copilot', 'cursor']) {
    assert.strictEqual(sessionRun.runsInOneSession(name, REPO), true, `${name} は 1 セッション`);
  }
  // ローカルは工程ごとに起こしても費用が増えないので、検査と証跡のあるハーネスのまま。
  for (const name of ['aider', 'ollama']) {
    assert.strictEqual(sessionRun.runsInOneSession(name, REPO), false, `${name} はハーネス`);
  }
  // クラウドでも自分でツールを回せない定義へ発動文を送っても、返るのは答えであって実行ではない。
  assert.strictEqual(sessionRun.runsInOneSession('vscode-copilot', REPO), false);
  // 読めない定義・空は従来どおり（分からないときは切り替えない）。
  assert.strictEqual(sessionRun.runsInOneSession('存在しない定義', REPO), false);
  assert.strictEqual(sessionRun.runsInOneSession('', REPO), false);
});

test('費用はモデル単位の宣言が定義単位より優先する（agentcore と同じ規則）', () => {
  const spec = { defaultModel: 'big', relativeCost: 1, models: { small: { relative_cost: 0 } } };
  assert.strictEqual(agentCli.relativeCost(spec), 1);
  assert.strictEqual(agentCli.relativeCost(spec, 'small'), 0);
  assert.strictEqual(agentCli.isLocal(spec, 'small'), true);
  assert.strictEqual(agentCli.isLocal(spec), false);
  // 宣言が壊れている定義は通常のクラウド扱い（schema の既定と同じ）。
  assert.strictEqual(agentCli.relativeCost({ relativeCost: NaN, models: {} }), 1);
});

test('送る 1 行は条件を並べ、共通指示はその前に置く', () => {
  assert.strictEqual(
    sessionRun.invocation({ machine: 'digest', parameters: { topic: 'llm', 空: '  ' } }),
    'statemachine-use スキルでdigestステートマシンを実行して\n\n入力:\n- topic: llm',
  );
  assert.strictEqual(
    sessionRun.invocation({ machine: 'digest' }),
    'statemachine-use スキルでdigestステートマシンを実行して',
  );
  const withCommon = sessionRun.invocation({ machine: 'digest', instruction: '## 共通指示\n丁寧に' });
  assert.ok(withCommon.startsWith('## 共通指示\n丁寧に\n\n'), '共通指示は発動文の前');
  assert.ok(withCommon.endsWith('ステートマシンを実行して'));
  assert.throws(() => sessionRun.invocation({ machine: '../外' }), /識別名/);
});

test('文面は agentcore の 1 実装と同じ綴り（ずれたら落とす）', (t) => {
  const core = path.join(REPO, 'tools', 'agent-tools', 'agentcore');
  if (!fs.existsSync(path.join(core, 'agentcore', 'loopentry.py'))) return t.skip('agentcore が無い');
  const script = [
    'import sys, json',
    `sys.path.insert(0, ${JSON.stringify(core)})`,
    'from agentcore import loopentry',
    'spec = loopentry.statemachine_spec({"statemachine": "digest", "input": {"topic": "llm"}})',
    'print(json.dumps(loopentry.statemachine_command(spec, slash=False)))',
  ].join('\n');
  let canonical;
  try {
    canonical = JSON.parse(execFileSync('python3', ['-c', script], { encoding: 'utf8' }).trim());
  } catch {
    return t.skip('python3 を起動できない');
  }
  assert.strictEqual(sessionRun.invocation({ machine: 'digest', parameters: { topic: 'llm' } }), canonical);
});

test('起動仕様は定義から組み、本文の渡し方も定義に従う', () => {
  const kiro = sessionRun.runSpec({ root: REPO, machine: 'digest', agent: 'kiro', model: 'auto' });
  assert.strictEqual(kiro.command, 'kiro-cli');
  assert.deepStrictEqual(kiro.args.slice(0, 4), ['chat', '--no-interactive', '--trust-all-tools', '--model']);
  assert.strictEqual(kiro.args[kiro.args.length - 1], kiro.prompt, '本文は最後の引数');
  assert.strictEqual(kiro.input, '');
  assert.strictEqual(kiro.host, true, 'CLI 本体は Windows では WSL 側に居る');

  // 本文を標準入力で受け、答えをファイルへ書く定義。置き場は実行ごとに割り当てる。
  const codex = sessionRun.runSpec({ root: REPO, machine: 'digest', agent: 'codex' });
  assert.strictEqual(codex.input, codex.prompt);
  assert.ok(!codex.args.includes(codex.prompt), '本文を引数へ重ねて渡さない');
  assert.ok(codex.outputFile, '応答の置き場を決める');
  assert.ok(!codex.args.some((a) => String(a).includes('{output_file}')), '置換の跡を残さない');
  assert.ok(codex.args.includes(codex.outputFile));

  assert.throws(() => sessionRun.runSpec({ root: REPO, machine: 'digest', agent: '' }), /AI を選んで/);
});

test('配線: 1 セッションでは結果を終了コードで見て、確認コマンドの宣言があれば言う', () => {
  const handlers = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'main', 'automation', 'handlers.js'), 'utf8');
  assert.match(handlers, /const oneSession = task\.kind === 'statemachine'\s*\n\s*&& sessionRun\.runsInOneSession\(/);
  assert.match(handlers, /if \(oneSession\) \{/);
  assert.match(handlers, /resultSource = 'exit-code';/);
  assert.match(handlers, /resultSource === 'result-line'\s*\n\s*\? agentLoop\.parseResult\(/);
  assert.match(handlers, /確認コマンド（\$\{checks\} 件）は実行されません/);
  assert.match(handlers, /stripDecoration \? terminalText\.stripAnsi\(line\) : line/);
});

// --- 実行時の配線 ---------------------------------------------------------------------
// 綴りの一致だけでは、変数の取り違えのような「動かしたら落ちる」配線を捕まえられない。
// electron と外部コマンドの起動を差し替えて、run:start を実際に 1 回通す。

function withStubbedHandlers(run) {
  const electronPath = require.resolve('electron');
  const runnerPath = require.resolve('../src/main/automation/runner');
  const handlersPath = require.resolve('../src/main/automation/handlers');
  const saved = { [electronPath]: require.cache[electronPath], [runnerPath]: require.cache[runnerPath] };
  const registered = new Map();
  const launches = [];
  const stub = (id, exports) => { require.cache[id] = { id, filename: id, loaded: true, exports }; };

  stub(electronPath, {
    ipcMain: { handle: (channel, fn) => registered.set(channel, fn) },
    dialog: { showOpenDialog: async () => ({ canceled: true, filePaths: [] }) },
    shell: { openPath: async () => '' },
    app: { getPath: () => '/tmp/agent-app-test-userdata' },
  });
  stub(runnerPath, {
    MAX_STREAM_OUTPUT: 1024,
    capture: async (name, args) => (
      name === 'agent-loop' && args[0] === 'inspect'
        ? { ok: true, status: 0, stderr: '', stdout: JSON.stringify({
          available: true, machines: [], history: [], daemon: { running: false },
          tasks: [{ id: 'machine:digest', kind: 'statemachine', machine: 'digest' }],
        }) }
        : { ok: false, status: 1, stdout: '', stderr: '', error: 'stub' }),
    stream: (command, args, options) => { launches.push({ command, args, options }); return { pid: 1 }; },
    stop: () => true,
    isRunning: () => false,
    startDetached: async () => ({ pid: 2 }),
    spawnRecorder: () => ({ pid: 3, wait: async () => ({ code: 0, stderr: '' }) }),
    createOutputCollector: () => ({ push() {}, finish() {}, result: () => ({ stdout: '', stderr: '' }) }),
  });
  delete require.cache[handlersPath];
  try {
    return run({ handlers: require(handlersPath), registered, launches });
  } finally {
    delete require.cache[handlersPath];
    for (const [id, entry] of Object.entries(saved)) {
      if (entry) require.cache[id] = entry; else delete require.cache[id];
    }
  }
}

async function startRun(agent) {
  return withStubbedHandlers(async ({ handlers, registered, launches }) => {
    const spawnSpecCalls = [];
    handlers.registerIpcHandlers(() => null, {
      channelPrefix: 'automation:',
      userData: () => '/tmp/agent-app-test-userdata',
      appRoot: path.join(__dirname, '..'),
      config: {
        load: () => ({ roots: [REPO], lastRoot: REPO, skillDir: '', agent, model: '' }),
        save: (_u, c) => c,
        addRoot: (_u, r) => r,
        removeRoot: (_u, r) => r,
        isRegistered: () => true,
      },
      agentDefinitions: async () => ['kiro', 'aider', 'herd'],
      // 起動をどちら側（この端末 / WSL）へ載せるかは、ここへ渡る host で決まる。
      commandSpawnSpec: (name, opts) => { spawnSpecCalls.push({ name, ...(opts || {}) }); return undefined; },
      hooks: {
        resolveAgent: async ({ agent: name }) => ({ agent: name }),
        prepareRun: async () => ({ instruction: '', information: [], warning: '' }),
      },
    });
    const start = registered.get('automation:run:start');
    assert.ok(start, 'run:start が登録される');
    const sender = { isDestroyed: () => false, send: () => {} };
    // 登録した実体は ipcMain.handle の (event, args) 版なので、その順で呼ぶ。
    const result = await start({ sender },
      { root: REPO, machine: 'digest', taskId: 'machine:digest', mode: 'run', agent });
    assert.strictEqual(result.ok, true, result.error);
    assert.strictEqual(launches.length, 1, '1 回だけ起こす');
    return { ...launches[0], spawnSpecCalls };
  });
}

test('実行時: クラウドの CLI は発動文 1 つで直に起こす（ハーネスを経由しない）', async () => {
  const launch = await startRun('kiro');
  assert.strictEqual(launch.command, 'kiro-cli');
  assert.ok(!launch.args.includes('statemachine'), 'ハーネスのサブコマンドを使わない');
  assert.match(launch.args[launch.args.length - 1], /^statemachine-use スキルでdigestステートマシンを実行して$/);
  assert.ok(launch.spawnSpecCalls.some((c) => c.name === 'kiro-cli' && c.host === true),
    'CLI 本体は Windows では WSL 側で起こす');
});

test('実行時: ローカルの CLI は従来どおり工程ごとのハーネスで回す', async () => {
  const launch = await startRun('aider');
  assert.strictEqual(launch.command, 'agent-loop');
  assert.deepStrictEqual(launch.args.slice(0, 2), ['statemachine', '--workflow']);
  assert.ok(launch.args.includes('--agent-cli'));
  assert.strictEqual(launch.args[launch.args.indexOf('--agent-cli') + 1], 'aider');
});
