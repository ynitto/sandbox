'use strict';

// 定常業務の起動が「全体設定 → 実行制御」の機能別宣言（control.json の
// workloads.routine.agent_cli / model）どおりの対話 CLI を起こすことを固定する。
//
// 直った不具合: 定常業務だけが全体設定も ⚙ アシスタント設定も無視して、常に
// kiro-cli で起動していた。原因は 2 つ重なっていた。
//   1. cowork.chatCommand の**既定値**が 'kiro-cli chat --trust-all-tools' で、
//      saveConfig が既定値も config.json へ書き戻すため「明示上書きが常にある」状態になり、
//      CLI 解決（S9-3）へ一度も到達しなかった。
//   2. loopProvider が `{ cowork: cfg }` を組み直して解決を呼んでいたため、
//      orchestration（全体設定）も agent（⚙ 設定）もその時点で落ちていた。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const cowork = require('../src/features/cowork/main/cowork');
const loopProvider = require('../src/features/cowork/main/loopProvider');
const agent = require('../src/features/agent-project/main/agent');
const agentCli = require('../src/features/agent-project/main/agentCli');
const coworkDefaults = require('../src/features/cowork/config');
const wslMain = require('../src/base/main/wsl');

// win32 の起動は「窓を開く前に wsl.exe を実地検査する」。テスト機に WSL は無いので
// 検査だけ差し替え、起動コマンドの組み立てとスクリプト本体を見る。
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// 全体設定（agent-control 契約）の control.json を書いた一時ディレクトリを設定へ載せる。
function configWithControl(control, extra = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'routine-control-'));
  fs.writeFileSync(path.join(dir, 'control.json'),
    `${JSON.stringify({ version: 1, revision: 1, ...control }, null, 2)}\n`);
  return { ...extra, orchestration: { ...(extra.orchestration || {}), controlDir: dir } };
}

// 空の作業フォルダ。プロジェクト設定（agent-project.yaml）を拾わせないために使う。
function emptyRepo() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'routine-repo-'));
}

function withWin32(fn) {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  try {
    return fn();
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
}

test('cowork.chatCommand の既定は空（＝明示上書きなし）', () => {
  // 既定値を入れると saveConfig が全ユーザーの config.json へ書き戻し、
  // 「誰も触っていないのに常に上書きが載っている」状態になる。
  assert.strictEqual(coworkDefaults.cowork.chatCommand, '',
    '既定で CLI を固定しない（全体設定の解決へ委ねる）');
});

test('全体設定の workloads.routine で指定したエージェントとモデルで定常業務を起動する', () => {
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } },
  });
  const launch = cowork.coworkChatLaunch(config, emptyRepo());
  assert.strictEqual(launch.cli, 'ollama');
  assert.strictEqual(launch.model, 'qwen3:8b');
  // agents/ollama.json の interactive 定義どおりの argv（モデル名は argv の一部）。
  assert.deepStrictEqual(launch.chatCommand,
    ['agent-ollama', '--tui', '--think', 'off', 'qwen3:8b']);
  // 入力受付の待ち方も CLI 定義から来る（kiro の固定パターンを使い回さない）。
  assert.strictEqual(launch.readyPattern, '^[[:space:]]*>[[:space:]]*$');
});

test('全体設定の宣言は ⚙ アシスタント設定より優先する', () => {
  const config = configWithControl(
    { workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } } },
    { agent: { cli: 'claude', model: 'sonnet' } }
  );
  const launch = cowork.coworkChatLaunch(config, emptyRepo());
  assert.strictEqual(launch.cli, 'ollama', '管理面の宣言が最優先');
  assert.strictEqual(launch.source, 'control');
  // 他機能（workload を渡さない CLIチャット等）の解決は従来どおり ⚙ 設定のまま。
  const chat = agent.interactiveLaunchSpec(config, emptyRepo());
  assert.strictEqual(chat.cli, 'claude', 'routine の宣言を他の経路へ漏らさない');
});

test('workloads.routine が空欄なら defaults（全機能共通）→ ⚙ 設定 の順で委ねる', () => {
  const viaDefaults = cowork.coworkChatLaunch(configWithControl(
    { defaults: { agent_cli: 'ollama' }, workloads: { routine: { agent_cli: null } } },
    { agent: { cli: 'claude' } }
  ), emptyRepo());
  assert.strictEqual(viaDefaults.cli, 'ollama', '空欄は defaults へ委ねる');

  const viaSettings = cowork.coworkChatLaunch(configWithControl(
    { workloads: { routine: { agent_cli: '', model: '' } } },
    { agent: { cli: 'claude' } }
  ), emptyRepo());
  assert.strictEqual(viaSettings.cli, 'claude', 'どちらも空欄なら ⚙ 設定へ委ねる');
});

test('全体設定がモデルだけの宣言なら、CLI は下位の解決のままモデルだけ差し替える', () => {
  const config = configWithControl(
    { workloads: { routine: { model: 'llama3.1' } } },
    { agent: { cli: 'ollama', model: 'qwen3' } }
  );
  const launch = cowork.coworkChatLaunch(config, emptyRepo());
  assert.strictEqual(launch.cli, 'ollama');
  assert.strictEqual(launch.model, 'llama3.1');
  assert.deepStrictEqual(launch.chatCommand,
    ['agent-ollama', '--tui', '--think', 'off', 'llama3.1']);
});

test('全体設定に定義の無いエージェント名が入っていても定常業務を止めない（警告して下位へ）', () => {
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (msg) => warnings.push(String(msg));
  try {
    const launch = cowork.coworkChatLaunch(configWithControl(
      { workloads: { routine: { agent_cli: 'no-such-cli' } } },
      { agent: { cli: 'claude' } }
    ), emptyRepo());
    assert.strictEqual(launch.cli, 'claude', '解決できない宣言は下位へ倒す');
  } finally {
    console.warn = realWarn;
  }
  assert.ok(warnings.some((w) => w.includes('no-such-cli')),
    '黙って別の CLI で走らせず、設定ミスを警告に残す');
});

test('旧既定値の chatCommand は上書きとして扱わない（人が書いた上書きは効く）', () => {
  const control = { workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } } };
  const legacy = cowork.coworkChatLaunch(
    configWithControl(control, { cowork: { chatCommand: cowork.LEGACY_CHAT_COMMAND } }),
    emptyRepo()
  );
  assert.strictEqual(legacy.cli, 'ollama', '既定値の残骸は無視して全体設定を読む');

  const explicit = cowork.coworkChatLaunch(
    configWithControl(control, { cowork: { chatCommand: 'my-cli chat' } }),
    emptyRepo()
  );
  assert.deepStrictEqual(explicit.chatCommand, 'my-cli chat', '人が書いた上書きはそのまま');
});

test('セッション開始コマンドの when.agent_cli は解決済みの CLI で判定する', () => {
  const sessionDir = fs.mkdtempSync(path.join(os.tmpdir(), 'routine-session-'));
  fs.writeFileSync(path.join(sessionDir, 'session.json'), `${JSON.stringify({
    version: 1,
    revision: 1,
    enabled: true,
    commands: [
      { id: 'for-ollama', mode: 'process', run: 'echo ollama', when: { agent_cli: ['ollama'] } },
      { id: 'for-kiro', mode: 'process', run: 'echo kiro', when: { agent_cli: ['kiro'] } },
    ],
  }, null, 2)}\n`);
  const config = configWithControl({ workloads: { routine: { agent_cli: 'ollama', model: 'qwen3' } } });
  config.orchestration.sessionDir = sessionDir;
  const plan = cowork.routineLaunchPlan(config, emptyRepo());
  const live = plan.sessionCommands.filter((e) => !e.skip).map((e) => e.id);
  assert.deepStrictEqual(live, ['for-ollama'],
    '起動する CLI 向けの開始コマンドだけを差し込む（kiro 固定にしない）');
});

test('定常業務の実行は全体設定で指定した CLI のウィンドウを開く（設定を落とさない）', () => {
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } },
  });
  config.cowork = { loopProvider: 'kiro-loop', loopCommand: 'kiro-loop' };
  withWin32(() => {
    const res = loopProvider.makeLoopProvider(config.cowork, config)
      .run({ id: '毎朝レビュー', cwd: emptyRepo(), prompt: 'レビューしてください' });
    assert.strictEqual(res.ok, true);
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-ollama' '--tui' '--think' 'off' 'qwen3:8b'"),
      `全体設定どおりの対話 CLI を tmux で起こす: ${body.slice(0, 400)}`);
    assert.ok(!body.includes('kiro-cli'), 'kiro-cli へ固定で倒れない');
    assert.ok(body.includes('レビューしてください'), '解決済みプロンプト本文を送る');
  });
});

test('定常業務一覧からの実行（runLoop）も全体設定どおりの CLI で起動する', () => {
  const repo = emptyRepo();
  fs.mkdirSync(path.join(repo, '.kiro'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.kiro', 'kiro-loop.yml'), [
    'prompts:',
    '  - name: "毎朝レビュー"',
    '    prompt: 直近の変更をレビューしてください',
    '    interval_minutes: 60',
    '',
  ].join('\n'));
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } },
  });
  config.cowork = {
    loopProvider: 'kiro-loop',
    loopCommand: 'kiro-loop',
    items: [{ id: '毎朝レビュー', type: 'loop', name: '毎朝レビュー', repo }],
  };
  withWin32(() => {
    const res = cowork.runLoop(config, '毎朝レビュー');
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-ollama' '--tui' '--think' 'off' 'qwen3:8b'"),
      '一覧から実行しても全体設定の CLI で起こす');
    assert.ok(body.includes('直近の変更をレビューしてください'), '定期プロンプト本文を送る');
  });
});

test('呼び出し側が解決済みの起動条件を渡したら、実行側は再解決しない', () => {
  // 開始コマンドの計画と実際の起動が別々に解決すると、送る先と送る中身がずれる。
  const launch = {
    chatCommand: ['agent-ollama', '--tui', 'qwen3'], cli: 'ollama', skillCommandPrefix: '/',
  };
  withWin32(() => {
    const res = loopProvider.makeLoopProvider({ loopCommand: 'kiro-loop' })
      .run({ id: 'X', cwd: emptyRepo(), prompt: 'やって', launch });
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-ollama' '--tui' 'qwen3'"), '渡された起動条件をそのまま使う');
  });
});

test('resolveAgent は workload を渡さない呼び出しでは control.json を読まない', () => {
  // charter 補完・Doctor 等は機能別宣言の対象外。routine の宣言で挙動が変わってはいけない。
  const config = configWithControl(
    { workloads: { routine: { agent_cli: 'ollama' } } },
    { agent: { cli: 'claude' } }
  );
  assert.strictEqual(agent.resolveAgent(config, emptyRepo()).cli, 'claude');
  assert.strictEqual(agent.resolveAgent(config, emptyRepo(), { workload: 'routine' }).cli, 'ollama');
});

test('readControlAgent は control.json が無くても空を返す（起動を止めない）', () => {
  const missing = path.join(os.tmpdir(), 'routine-control-absent-does-not-exist');
  assert.deepStrictEqual(
    agent.readControlAgent({ orchestration: { controlDir: missing } }, 'routine'),
    { workload: 'routine', cli: '', model: '' }
  );
  assert.deepStrictEqual(agent.readControlAgent({}, ''), {}, 'workload 未指定は読まない');
});

agentCli.clearCache();
console.log(`\n${passed} tests passed`);
