'use strict';

// 「準備の確認」は CLI が起動するだけでなく、操作記録のコマンドを持つことまで確かめる。

const { test } = require('node:test');
const assert = require('node:assert');
const tools = require('../src/main/tools');

function baseCapture(help) {
  return async (command, args) => {
    if (command === 'python3') return { ok: true, status: 0, stdout: 'Python 3.13.0', stderr: '' };
    if (command === 'agent-loop') return { ok: true, status: 0, stdout: 'agent-loop 1.2.3', stderr: '' };
    if (command === 'playwright-cli' && args.includes('--version')) return { ok: true, status: 0, stdout: '0.1.18', stderr: '' };
    if (command === 'playwright-cli' && args.includes('--help')) return { ok: true, status: 0, stdout: help, stderr: '' };
    return { ok: false, status: 1, stdout: '', stderr: '' };
  };
}

test('準備の確認は記録コマンドの無い playwright-cli を利用不可にする', async () => {
  const statuses = await tools.toolStatus({ capture: baseCapture('open [url]\ntracing-start\n') });
  const pw = statuses.find((item) => item.id === 'playwright-cli');
  assert.strictEqual(pw.ok, false);
  assert.match(pw.summary, /0\.1\.18.*操作の記録に未対応/);
  assert.match(pw.hint, /@latest/);
});

test('準備の確認は recording-start と recording-stop の両方があれば利用可能にする', async () => {
  const statuses = await tools.toolStatus({ capture: baseCapture('recording-start\nrecording-stop\n') });
  const pw = statuses.find((item) => item.id === 'playwright-cli');
  assert.strictEqual(pw.ok, true);
  assert.match(pw.summary, /利用可能.*0\.1\.18/);
  assert.strictEqual(pw.hint, '');
});

test('準備の確認に自動実行基盤の状態を含める', async () => {
  const statuses = await tools.toolStatus({ capture: baseCapture('recording-start\nrecording-stop\n') });
  const loop = statuses.find((item) => item.id === 'agent-loop');
  assert.strictEqual(loop.ok, true);
  assert.match(loop.summary, /agent-loop 1\.2\.3/);
});

test('使うAIは agent-tools の定義一覧から取得する', async () => {
  const calls = [];
  const definitions = await tools.agentDefinitions({
    cwd: '/project',
    capture: async (command, args, options) => {
      calls.push({ command, args, options });
      return {
        ok: true,
        status: 0,
        stdout: JSON.stringify({ definitions: ['claude', 'codex', 'custom', 'claude'] }),
        stderr: '',
      };
    },
  });

  assert.deepStrictEqual(definitions, ['claude', 'codex', 'custom']);
  assert.deepStrictEqual(calls, [{
    command: 'agent-herd',
    args: ['defs', '--json'],
    options: { cwd: '/project', timeoutMs: 20000 },
  }]);
});

test('AI支援は agent-tools を読み取り専用・単発で起動する', () => {
  const spec = tools.agentAssistRunSpec({
    root: '/project',
    agent: 'codex',
    model: 'gpt-5',
    prompt: 'JSON だけを返してください',
  });

  assert.deepStrictEqual(spec, {
    command: 'agent-herd',
    args: [
      '--agent', 'codex',
      '--purpose', 'plan',
      '--readonly',
      '--dir', '/project',
      '--model', 'gpt-5',
      '-p', 'JSON だけを返してください',
    ],
  });
});
