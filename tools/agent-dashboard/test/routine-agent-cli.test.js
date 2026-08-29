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

// 開発端末の ~/.agents/agents に依存させず、クリーンな CI でも同梱定義を読む。
process.env.KIRO_AGENTS_DIR = path.resolve(__dirname, '..', '..', '..', 'agents');

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

function writeProfiles(config, profiles) {
  const dir = config.orchestration.controlDir;
  fs.writeFileSync(path.join(dir, 'profiles.json'), `${JSON.stringify({
    version: 1,
    enabled: true,
    policy: { apply_to: ['routine'], steps: [] },
    ...profiles,
  }, null, 2)}\n`);
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
    ['agent-herd', 'ollama', '--tui', '--think', 'on', 'qwen3:8b']);
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
    ['agent-herd', 'ollama', '--tui', '--think', 'on', 'llama3.1']);
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

test('今すぐ実行の段は候補を同じ段内で解決し、設定へ保存しない', () => {
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'old-model' } },
  });
  writeProfiles(config, {
    tiers: {
      medium: { order: 20, label: '中', candidates: [{ agent_cli: 'ollama', model: 'qwen3:8b' }] },
      small: { order: 10, label: '小', candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }] },
    },
    state: { routine: { tier: 'small', candidate: { agent_cli: 'aider', model: 'gemma4:e4b' } } },
  });
  const before = fs.readFileSync(path.join(config.orchestration.controlDir, 'profiles.json'), 'utf8');
  const ov = cowork.overview(config);
  assert.deepStrictEqual(ov.routineTiers.map((t) => [t.id, t.agent_cli, t.model]), [
    ['medium', 'ollama', 'qwen3:8b'],
    ['small', 'aider', 'gemma4:e4b'],
  ]);
  assert.strictEqual(ov.currentRoutineTier, 'small');
  const selected = cowork.resolveRoutineAgent(config, emptyRepo(), 'small');
  assert.strictEqual(selected.cli, 'aider');
  assert.strictEqual(selected.model, 'gemma4:e4b');
  assert.strictEqual(fs.readFileSync(path.join(config.orchestration.controlDir, 'profiles.json'), 'utf8'), before,
    '一回限りの選択は profiles.json を変更しない');
  assert.throws(() => cowork.resolveRoutineAgent(config, emptyRepo(), 'missing'), /段.*missing/);
});

test('今すぐ実行のモデル空欄は選択した CLI の既定に任せる', () => {
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'codex', model: 'gpt-5.6-luna' } },
  });
  writeProfiles(config, {
    tiers: {
      basic: { order: 10, label: '単純作業', candidates: [{ agent_cli: 'aider' }] },
    },
  });

  const repo = emptyRepo();
  const selected = cowork.resolveRoutineAgent(config, repo, {
    tier: 'basic', agent_cli: 'aider', model: '',
  });
  assert.strictEqual(selected.cli, 'aider');
  assert.strictEqual(selected.model, '',
    '別 CLI の自動割り当てモデルを引き継がない');
  assert.ok(!cowork.stateMachineHarnessArgs(repo, path.join(repo, 'workflow.yaml'), selected, {}, {})
    .includes('--model'), '空欄なら agent-loop / CLI 定義の default_model に任せる');
});

test('今すぐ実行は全実行レベルの全候補を表示し、選んだ組み合わせをそのまま解決する', () => {
  const config = configWithControl({ workloads: { routine: {} } });
  writeProfiles(config, {
    tiers: {
      medium: {
        order: 20,
        label: '標準',
        candidates: [
          { agent_cli: 'ollama', model: 'qwen3:8b' },
          { agent_cli: 'aider', model: 'gemma4:e2b' },
        ],
      },
      small: {
        order: 10,
        label: '軽量',
        candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }],
      },
    },
    state: {
      routine: {
        tier: 'medium',
        candidate: { agent_cli: 'aider', model: 'gemma4:e2b' },
      },
    },
  });

  const ov = cowork.overview(config);
  assert.deepStrictEqual(ov.routineTiers.map((choice) => [choice.id, choice.agent_cli, choice.model]), [
    ['medium', 'ollama', 'qwen3:8b'],
    ['medium', 'aider', 'gemma4:e2b'],
    ['small', 'aider', 'gemma4:e4b'],
  ]);
  assert.deepStrictEqual(ov.currentRoutineCandidate, { agent_cli: 'aider', model: 'gemma4:e2b' });

  const selected = cowork.resolveRoutineAgent(config, emptyRepo(), {
    tier: 'medium', agent_cli: 'aider', model: 'gemma4:e2b',
  });
  assert.strictEqual(selected.cli, 'aider');
  assert.strictEqual(selected.model, 'gemma4:e2b');
  assert.throws(() => cowork.resolveRoutineAgent(config, emptyRepo(), {
    tier: 'medium', agent_cli: 'aider', model: 'not-configured',
  }), /候補.*定義されていません/);
});

test('業務ごとの実行エージェント設定は自動割り当てより優先し、一覧が両方を見せる', () => {
  const repo = emptyRepo();
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'base-model' } },
  }, {
    cowork: {
      items: [{
        id: 'daily-report', type: 'loop', name: 'daily-report', repo, prompt: '日報を書く',
        executionChoice: { tier: 'small', agent_cli: 'aider', model: 'gemma4:e4b' },
      }],
    },
  });
  writeProfiles(config, {
    tiers: {
      medium: { order: 20, label: '標準', candidates: [{ agent_cli: 'ollama', model: 'qwen3:8b' }] },
      small: { order: 10, label: '軽量', candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }] },
    },
  });
  const item = cowork.resolveItem(config, 'daily-report');
  assert.deepStrictEqual(cowork.storedExecutionChoice(config, item),
    { tier: 'small', agent_cli: 'aider', model: 'gemma4:e4b' });
  const row = cowork.overview(config).items.find((it) => it.id === 'daily-report');
  assert.strictEqual(row.executionSource, 'user');
  assert.deepStrictEqual(row.execution, { agent_cli: 'aider', model: 'gemma4:e4b' },
    '一覧・詳細は設定どおりの実効エージェントを見せる');
  assert.deepStrictEqual(row.autoExecution, { agent_cli: 'ollama', model: 'base-model' },
    '自動割り当てなら何になるかも編集画面向けに出す');
  assert.deepStrictEqual(row.executionChoice, { tier: 'small', agent_cli: 'aider', model: 'gemma4:e4b' });

  // 設定の無い項目は従来どおり自動割り当て
  const auto = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'base-model' } },
  }, { cowork: { items: [{ id: 'plain', type: 'loop', name: 'plain', repo, prompt: 'x' }] } });
  const autoRow = cowork.overview(auto).items.find((it) => it.id === 'plain');
  assert.strictEqual(autoRow.executionSource, 'auto');
  assert.deepStrictEqual(autoRow.execution, { agent_cli: 'ollama', model: 'base-model' });
});

test('今すぐ実行の明示選択は project YAML と control の候補より優先する', () => {
  const repo = emptyRepo();
  fs.writeFileSync(path.join(repo, 'agent-project.yaml'),
    'agent_cli: claude\nmodel: yaml-model\n');
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'control-model' } },
  });
  writeProfiles(config, {
    tiers: {
      large: {
        order: 30,
        label: '高性能',
        candidates: [{ agent_cli: 'codex', model: 'dashboard-model' }],
      },
    },
  });
  const selected = cowork.resolveRoutineAgent(config, repo, {
    tier: 'large', agent_cli: 'codex', model: 'dashboard-model',
  });
  assert.strictEqual(selected.cli, 'codex');
  assert.strictEqual(selected.model, 'dashboard-model');
  assert.strictEqual(selected.source, 'tier:large');
});

test('発見項目の実行エージェント設定は対応表で持ち、実体ファイルを変更しない', () => {
  const config = configWithControl({ workloads: { routine: { agent_cli: 'ollama' } } }, {
    cowork: {
      executionChoices: {
        'disc:loop:repo:daily': { tier: 'medium', agent_cli: 'ollama', model: 'qwen3:8b' },
        broken: { tier: '', agent_cli: 'x' }, // 形の崩れた対応は無視する
      },
    },
  });
  assert.deepStrictEqual(
    cowork.storedExecutionChoice(config, { id: 'disc:loop:repo:daily', source: 'discovered' }),
    { tier: 'medium', agent_cli: 'ollama', model: 'qwen3:8b' }
  );
  assert.strictEqual(
    cowork.storedExecutionChoice(config, { id: 'broken', source: 'discovered' }), null);
  assert.strictEqual(
    cowork.storedExecutionChoice(config, { id: 'unknown', source: 'discovered' }), null);
});

test('saveWork は手動項目の設定を残し、発見項目の設定を対応表として保存する', () => {
  const config = { cowork: {} };
  let savedConfig = null;
  const saveConfig = (next) => { savedConfig = next; return next; };
  cowork.saveWork(config, saveConfig, {
    items: [
      {
        id: 'daily-report', type: 'loop', name: 'daily-report', repo: '', prompt: '日報',
        source: 'config', state: { status: 'idle' }, parameters: [], parameterError: '',
        execution: { agent_cli: 'aider', model: 'gemma4:e4b' }, executionSource: 'user',
        autoExecution: { agent_cli: 'ollama', model: 'base' },
        executionChoice: { tier: 'small', agent_cli: 'aider', model: 'gemma4:e4b' },
      },
      {
        id: 'disc:loop:repo:sync', type: 'loop', name: 'sync', repo: '', prompt: 'x',
        source: 'discovered', _src: { kind: 'loop', file: '/no/such/file', format: 'yaml' },
        executionChoice: { tier: 'medium', agent_cli: 'ollama', model: 'qwen3:8b' },
      },
    ],
  });
  const [manual] = savedConfig.cowork.items;
  assert.deepStrictEqual(manual.executionChoice,
    { tier: 'small', agent_cli: 'aider', model: 'gemma4:e4b' }, '手動項目は項目自身に設定を持つ');
  assert.ok(!('execution' in manual) && !('parameters' in manual) && !('autoExecution' in manual),
    '実行時フィールドは保存しない');
  assert.deepStrictEqual(savedConfig.cowork.executionChoices,
    { 'disc:loop:repo:sync': { tier: 'medium', agent_cli: 'ollama', model: 'qwen3:8b' } },
    '発見項目の設定は対応表として dashboard 設定に持つ');
});

test('定常業務の実行は全体設定で指定した CLI のウィンドウを開く（設定を落とさない）', () => {
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'ollama', model: 'qwen3:8b' } },
  });
  config.cowork = { loopProvider: 'agent-loop', loopCommand: 'agent-loop' };
  withWin32(() => {
    const res = loopProvider.makeLoopProvider(config.cowork, config)
      .run({ id: '毎朝レビュー', cwd: emptyRepo(), prompt: 'レビューしてください' });
    assert.strictEqual(res.ok, true);
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-herd' 'ollama' '--tui' '--think' 'on' 'qwen3:8b'"),
      `全体設定どおりの対話 CLI を tmux で起こす: ${body.slice(0, 400)}`);
    assert.ok(!body.includes('kiro-cli'), 'kiro-cli へ固定で倒れない');
    assert.ok(body.includes('レビューしてください'), '解決済みプロンプト本文を送る');
  });
});

test('定常業務一覧からの実行（runLoop）も全体設定どおりの CLI で起動する', () => {
  const repo = emptyRepo();
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'agent-loop.yml'), [
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
    loopProvider: 'agent-loop',
    loopCommand: 'agent-loop',
    items: [{ id: '毎朝レビュー', type: 'loop', name: '毎朝レビュー', repo }],
  };
  withWin32(() => {
    const res = cowork.runLoop(config, '毎朝レビュー');
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-herd' 'ollama' '--tui' '--think' 'on' 'qwen3:8b'"),
      '一覧から実行しても全体設定の CLI で起こす');
    assert.ok(body.includes('直近の変更をレビューしてください'), '定期プロンプト本文を送る');
  });
});

test('実行レベルの `herd` は実測が無くても具体候補へ展開する（見せる・起こす）', () => {
  // 人が書くのは「ローカルを使うか」の 1 語だけ（aider と ollama の使い分けは用途ごとに
  // 違うので、人に選ばせると必ずどれかの用途で外れる）。展開は実測（qualifications）が
  // 決める。展開しないまま見せると `herd` が選べてしまい、起こす段で
  // 「agents/herd.json が無い」で落ちる。
  const config = configWithControl({ workloads: { routine: { agent_cli: 'kiro' } } });
  writeProfiles(config, {
    tiers: { basic: { order: 1, label: '単純作業', candidates: [{ agent_cli: 'herd', model: '' }] } },
  });
  const dir = config.orchestration.controlDir;
  // **実測（qualifications.json）が無くても回る。** 実測が決めるのは「どの用途にどれが
  // 向くか」で、一族が使えるかどうかではない。ここで止めると、herd と書いた人が
  // 管理面のファイルを自分で用意させられる。
  const withoutMeasurements = cowork.resolveRoutineAgent(config, emptyRepo(), 'basic');
  assert.ok(['aider', 'ollama'].includes(withoutMeasurements.cli), withoutMeasurements.cli);
  assert.strictEqual(withoutMeasurements.model, 'gemma4:e4b', '定義の既定モデルで展開する');
  fs.writeFileSync(path.join(dir, 'qualifications.json'), `${JSON.stringify({
    version: 1, revision: 1,
    candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }, { agent_cli: 'ollama', model: 'gemma4:e4b' }],
  })}\n`);
  const resolved = cowork.resolveRoutineAgent(config, emptyRepo(), 'basic');
  assert.ok(['aider', 'ollama'].includes(resolved.cli), `一族の実在メンバーへ解ける: ${resolved.cli}`);
  assert.strictEqual(resolved.model, 'gemma4:e4b');
  const rows = cowork.overview({ ...config, cowork: {} }).routineTiers;
  assert.deepStrictEqual(rows.map((r) => r.agent_cli).sort(), ['aider', 'ollama'],
    '一覧にも具体候補で出す（herd の 1 語を選ばせない）');
  // 宣言そのものは書き換えない（実測が更新されたら追従できなくなる）。
  assert.match(fs.readFileSync(path.join(dir, 'profiles.json'), 'utf8'), /"agent_cli": "herd"/);
});

test('クラウド CLI の定常業務も同じく対話ペインで起こす', () => {
  // 経路は 1 本（tmux + send-keys）。一族・クラウドで変わるのは送る本文だけ。
  const repo = emptyRepo();
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'agent-loop.yml'), [
    'prompts:',
    '  - name: "毎朝レビュー"',
    '    prompt: 直近の変更をレビューしてください',
    '    interval_minutes: 60',
    '',
  ].join('\n'));
  const config = configWithControl({
    workloads: { routine: { agent_cli: 'kiro', model: 'claude-haiku-4.5' } },
  });
  config.cowork = {
    loopProvider: 'agent-loop',
    loopCommand: 'agent-loop',
    items: [{ id: '毎朝レビュー', type: 'loop', name: '毎朝レビュー', repo }],
  };
  withWin32(() => {
    const body = fs.readFileSync(cowork.runLoop(config, '毎朝レビュー').scriptFile, 'utf8');
    assert.ok(body.includes("'kiro-cli' 'chat'"), '対話 CLI をそのまま起こす');
    assert.ok(!body.includes("'agent-loop' 'run'"), 'ハーネスへは回さない');
  });
});

test('呼び出し側が解決済みの起動条件を渡したら、実行側は再解決しない', () => {
  // 開始コマンドの計画と実際の起動が別々に解決すると、送る先と送る中身がずれる。
  const launch = {
    chatCommand: ['agent-herd', 'ollama', '--tui', 'qwen3'], cli: 'ollama', skillCommandPrefix: '/',
  };
  withWin32(() => {
    const res = loopProvider.makeLoopProvider({ loopCommand: 'agent-loop' })
      .run({ id: 'X', cwd: emptyRepo(), prompt: 'やって', launch });
    const body = fs.readFileSync(res.scriptFile, 'utf8');
    assert.ok(body.includes("'agent-herd' 'ollama' '--tui' 'qwen3'"), '渡された起動条件をそのまま使う');
  });
});

test('resolveAgent は workload を渡さない呼び出しでは control.json を読まない', () => {
  // workload を明示しない低レベル呼び出しは従来どおりツール設定へ委ねる。
  const config = configWithControl(
    { workloads: { routine: { agent_cli: 'ollama' } } },
    { agent: { cli: 'claude' } }
  );
  assert.strictEqual(agent.resolveAgent(config, emptyRepo()).cli, 'claude');
  assert.strictEqual(agent.resolveAgent(config, emptyRepo(), { workload: 'routine' }).cli, 'ollama');
});

test('Dashboard内AIはdashboard workloadとpurpose別controlを優先する', () => {
  const config = configWithControl(
    { workloads: { dashboard: {
      agent_cli: 'claude', model: 'sonnet', agents: { draft: { model: 'haiku' } },
    } } },
    { agent: { cli: 'codex', model: 'gpt-5' } }
  );
  const resolved = agent.resolveAgent(config, emptyRepo(), { workload: 'dashboard', purpose: 'draft' });
  assert.strictEqual(resolved.cli, 'claude');
  assert.strictEqual(resolved.model, 'haiku');
});

test('Dashboard内AIはdashboard workloadの上限到達時に実行しない', () => {
  const budgetDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-budget-'));
  fs.mkdirSync(path.join(budgetDir, 'ledger'));
  fs.writeFileSync(path.join(budgetDir, 'config.json'), `${JSON.stringify({
    version: 2,
    period: 'total',
    allocation: { workloads: { dashboard: { max_tokens: 10, on_exhausted: 'pause' } } },
  })}\n`);
  fs.writeFileSync(path.join(budgetDir, 'ledger', 'usage.jsonl'),
    `${JSON.stringify({ workload: 'dashboard', tokens_in: 11, tokens_out: 0 })}\n`);
  const config = configWithControl({ workloads: { dashboard: {} } },
    { orchestration: { budgetDir } });
  const execution = agent.dashboardExecutionState(config);
  assert.strictEqual(execution.exhausted, true);
  assert.strictEqual(execution.blocked, true);
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
