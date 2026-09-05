'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const setup = require('../src/main/sessionSetup');
const agentCli = require('../src/main/agentCli');

test('共通指示と推奨スキルを今回の依頼より前へまとめる', () => {
  const prompt = setup.withInstructions('画面を直して', {
    enabled: true,
    text: '回答は日本語で行う',
    skills: ['ui-designer', 'self-checking'],
  });
  assert.match(prompt, /^<!-- agent-app-instructions -->/);
  assert.ok(prompt.indexOf('回答は日本語で行う') < prompt.indexOf('画面を直して'));
  assert.match(prompt, /推奨スキル:\n- ui-designer\n- self-checking/);
  assert.match(prompt, /## 今回の依頼\n画面を直して$/);
});

test('開始アクションをCLI用スキル呼び出しと事前コマンドへ分ける', () => {
  const plan = setup.planActions([
    { type: 'skill', value: '/brainstorming', onError: 'warn' },
    { type: 'command', value: 'npm test', onError: 'fail' },
  ], { skillCommandPrefix: '$' });
  assert.deepStrictEqual(plan, {
    skillPrompt: '$brainstorming',
    commands: [{ command: 'npm test', onError: 'fail' }],
  });
});

test('エージェント定義からスキル呼び出し記号を取得する', () => {
  assert.strictEqual(agentCli.load('codex').skillCommandPrefix, '$');
  assert.strictEqual(agentCli.load('claude').skillCommandPrefix, '/');
});

test('開始コマンドは順番に実行し、warn は継続、fail は停止する', async () => {
  const calls = [];
  const run = async (command, timeoutMs) => {
    calls.push([command, timeoutMs]);
    return command === 'bad' ? { ok: false, status: 2, output: '失敗' } : { ok: true, status: 0, output: '完了' };
  };
  const result = await setup.runCommands([
    { command: 'bad', onError: 'warn' },
    { command: 'good', onError: 'fail' },
  ], run);
  assert.deepStrictEqual(calls, [['bad', 60000], ['good', 60000]]);
  assert.deepStrictEqual(result.information.map((item) => item.status), ['error', 'success']);
  assert.match(result.warning, /bad/);
  await assert.rejects(
    setup.runCommands([{ command: 'bad', onError: 'fail' }], run),
    (error) => error.code === 'STARTUP_ACTION_FAILED' && /bad/.test(error.message),
  );
  await assert.rejects(
    setup.runCommands([{ command: 'later', onError: 'warn' }], run, { totalMs: 0 }),
    (error) => error.code === 'STARTUP_ACTION_FAILED' && /タイムアウト/.test(error.message),
  );
});
