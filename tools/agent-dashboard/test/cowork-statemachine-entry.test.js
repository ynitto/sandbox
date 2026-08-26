'use strict';

// 定常業務エントリの `statemachine:` 宣言を、発見と「今すぐ実行」がどう扱うか。
//
// 見るのは 2 点。
//   1. 対の決め方が**宣言**になったこと（本文の言い回しからの推測は宣言が無いときだけ）
//   2. 実行条件（`input:` のマップ / `prompt` の自由文）が、入力欄ではなく
//      既定値として畳まれ、そのままハーネスの --param へ渡ること
//      ＝画面から回してもデーモンと同じ条件で動く
//
// 規則の正典は agentcore/loopentry.py と docs/specs/agent-loop-spec.md §2.3。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const wslMain = require('../src/base/main/wsl');
wslMain.verifyWslLaunch = () => ({ ok: true, error: '' });

// 走査ルートには常にユーザーホームが入る。実機のホームを覗かせない。
const HOME_STUB = fs.mkdtempSync(path.join(os.tmpdir(), 'sm-entry-home-'));
process.env.HOME = HOME_STUB;
process.env.USERPROFILE = HOME_STUB;

const cowork = require('../src/features/cowork/main/cowork');
const discover = require('../src/features/cowork/main/discover');

let passed = 0;
function test(name, fn) {
  const done = fn();
  const finish = () => { passed += 1; console.log(`ok - ${name}`); };
  return done && typeof done.then === 'function' ? done.then(finish) : Promise.resolve(finish());
}

const WORKFLOW = [
  'name: ダイジェスト',
  'description: 日次の要約',
  'initial_state: start',
  'context:',
  '  topic: ""',
  'states:',
  '  start:',
  '    action: "{{topic}} について {{input}}"',
  '    terminal: true',
  'transitions: []',
  '',
].join('\n');

function workspace(loopYaml, { machine = 'digest', workflow = WORKFLOW } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sm-entry-'));
  fs.mkdirSync(path.join(root, '.agents'), { recursive: true });
  fs.writeFileSync(path.join(root, '.agents', 'agent-loop.yml'), loopYaml, 'utf8');
  if (machine) {
    fs.mkdirSync(path.join(root, '.statemachine', machine), { recursive: true });
    fs.writeFileSync(path.join(root, '.statemachine', machine, 'workflow.yaml'), workflow, 'utf8');
  }
  return root;
}

// ハーネス経路（対話ペインを持たない CLI）へ倒し、起動 argv をそのまま stdout で見る。
function harnessConfig(root) {
  const controlDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sm-entry-ctl-'));
  fs.writeFileSync(path.join(controlDir, 'control.json'), `${JSON.stringify({
    version: 1, revision: 1, workloads: { routine: { agent_cli: 'aider' } },
  })}\n`);
  return {
    orchestration: { controlDir },
    cowork: { loopCommand: 'echo', runWindow: false, roots: [root], items: [] },
  };
}

function discovered(root, type = 'state-machine') {
  cowork.invalidateDiscoverCache();
  const items = discover.discoverCoworkItems({ cowork: { roots: [root] } });
  return items.find((it) => it.type === type);
}

async function main() {
  test('statemachine の宣言で対を決める（本文の言い回しに依存しない）', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次ダイジェスト',
      '    statemachine: digest',
      '    input:',
      '      topic: llm',
      '    prompt: 今日のぶんを書いて',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const items = discover.discoverCoworkItems({ cowork: { roots: [root] } });
    // 本文に「ステートマシン」の語も定義名も無いが、宣言があるので統合項目 1 件になる。
    assert.strictEqual(items.length, 1, '定期プロンプトと定型業務に割れない');
    const item = items[0];
    assert.strictEqual(item.type, 'state-machine');
    assert.strictEqual(item.workflow, 'digest');
    assert.strictEqual(item.schedule, '1440m', 'スケジュールは対の entry から引く');
    assert.strictEqual(item._src.loop.declared, true);
    assert.deepStrictEqual(item._src.loop.input, { topic: 'llm' });
  });

  test('宣言が無い設定は従来どおり本文から推測する（後方互換）', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次',
      '    prompt: statemachine-use スキルで digest ステートマシンを実行して',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const item = discovered(root);
    assert.strictEqual(item._src.loop.promptName, '日次', '推測でも対にはなる');
    assert.ok(!item._src.loop.declared, '宣言由来ではない');
  });

  test('宣言先の定義が無くても項目は消えない（設定を直す取っかかりを残す）', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次',
      '    statemachine: missing',
      '    interval_minutes: 60',
      '',
    ].join('\n'), { machine: null });
    const items = discover.discoverCoworkItems({ cowork: { roots: [root] } });
    assert.strictEqual(items.length, 1);
    assert.strictEqual(items[0].type, 'state-machine');
    assert.strictEqual(items[0].workflow, 'missing');
  });

  test('statemachineRef は agent-loop と同じ規則で正規化し、外を指す値は読まない', () => {
    assert.deepStrictEqual(discover.statemachineRef('digest'),
      { ref: '.statemachine/digest/workflow.yaml', name: 'digest' });
    assert.strictEqual(discover.statemachineRef('.statemachine/digest').ref,
      '.statemachine/digest/workflow.yaml');
    assert.strictEqual(discover.statemachineRef('flows/digest/machine.yml').ref,
      'flows/digest/machine.yml');
    for (const bad of ['../x', '/etc/passwd', '~/x', '', '..']) {
      assert.strictEqual(discover.statemachineRef(bad), null, bad);
    }
  });

  test('宣言済みの実行条件は入力を求めず、既定値として畳まれる', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次ダイジェスト',
      '    statemachine: digest',
      '    input:',
      '      topic: llm',
      '    prompt: 今日のぶんを書いて',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const item = discovered(root);
    const spec = cowork.routineParameterSpec({ cowork: { roots: [root] } }, item);
    assert.strictEqual(spec.error, '');
    assert.deepStrictEqual(spec.keys, [], '宣言済みの条件は入力欄に出さない');
    assert.strictEqual(spec.defaults.topic, 'llm');
    assert.strictEqual(spec.defaults.input, '今日のぶんを書いて',
      '自由文（prompt）は input パラメータ 1 個ぶんとして畳む');
    assert.deepStrictEqual(spec.entryInput, { topic: 'llm', input: '今日のぶんを書いて' },
      '宣言した条件は対話 CLI 経路のパラメータ節にも出す');
    assert.strictEqual(spec.entryDeclared, true);
  });

  test('宣言済みの entry は対話 CLI へスキル発動文を送り、自由文は条件として渡す', () => {
    // prompt は「実行条件」であって「指示文」ではない。そのまま送ると
    // ワークフローが起動しないまま自由文だけが会話へ流れる。
    const root = workspace([
      'prompts:',
      '  - name: 日次ダイジェスト',
      '    statemachine: digest',
      '    input:',
      '      topic: llm',
      '    prompt: 今日のぶんを書いて',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const config = { cowork: { roots: [root], runWindow: true, items: [] } };
    cowork.invalidateDiscoverCache();
    const item = discovered(root);
    const spec = cowork.routineParameterSpec(config, item);
    const block = cowork.stateMachineParameterBlock(spec.entryInput);
    assert.ok(block.includes('- topic: "llm"'));
    assert.ok(block.includes('- input: "今日のぶんを書いて"'),
      '自由文はパラメータ節の input として渡す');
  });

  test('宣言が無い対では prompt を実行条件にしない（意味を黙って変えない）', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次',
      '    prompt: digest ステートマシンを実行して',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const item = discovered(root);
    const spec = cowork.routineParameterSpec({ cowork: { roots: [root] } }, item);
    assert.ok(spec.keys.includes('input'), 'input は従来どおり人に訊く');
    assert.ok(!Object.prototype.hasOwnProperty.call(spec.defaults, 'input'));
  });

  test('宣言の一部だけなら、残りは従来どおり入力を求める', () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次',
      '    statemachine: digest',
      '    input:',
      '      topic: llm',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const item = discovered(root);
    const spec = cowork.routineParameterSpec({ cowork: { roots: [root] } }, item);
    assert.deepStrictEqual(spec.keys, ['input'], '自由文が無いので input だけ残る');
    assert.strictEqual(spec.defaults.topic, 'llm');
  });

  await test('今すぐ実行はデーモンと同じ実行条件でハーネスを起こす', async () => {
    const root = workspace([
      'prompts:',
      '  - name: 日次ダイジェスト',
      '    statemachine: digest',
      '    input:',
      '      topic: llm',
      '    prompt: 今日のぶんを書いて',
      '    interval_minutes: 1440',
      '',
    ].join('\n'));
    const config = harnessConfig(root);
    cowork.invalidateDiscoverCache();
    const item = cowork.overview(config).items.find((it) => it.type === 'state-machine');
    assert.ok(item, '統合項目が一覧に出る');
    assert.deepStrictEqual(item.parameters, [], '実行前に訊くことは残っていない');

    const res = await cowork.runStateMachine(config, item.id, {});
    assert.strictEqual(res.ok, true, res.error || res.stderr);
    const argv = res.stdout.trim().split(' ');
    assert.strictEqual(argv[0], 'statemachine');
    assert.ok(res.stdout.includes('--workflow .statemachine/digest/workflow.yaml'));
    assert.ok(res.stdout.includes('--param topic=llm'), '宣言したマップが条件になる');
    assert.ok(res.stdout.includes('--param input=今日のぶんを書いて'),
      '自由文は input パラメータとして渡る');
  });

  console.log(`\n${passed} tests passed`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
