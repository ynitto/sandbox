'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const loop = require('../src/main/agent-loop');

test('手動実行はagent-loopからagent-toolsのステートマシン実行へ渡す', () => {
  const spec = loop.runSpec({
    root: '/project', machine: 'review', agent: 'codex', model: 'gpt-5',
    parameters: { topic: '変更点', input: '今日の差分' },
  });

  assert.deepStrictEqual(spec, {
    command: 'agent-loop',
    args: [
      'statemachine', '--workflow', '.statemachine/review/workflow.yaml',
      '--dir', '/project', '--agent-cli', 'codex', '--model', 'gpt-5',
      '--param', 'input=今日の差分', '--param', 'topic=変更点',
    ],
  });
});

test('リポジトリの実行情報をagent-loopのJSON出力から取得する', async () => {
  const calls = [];
  const snapshot = await loop.inspect({
    root: '/project',
    capture: async (...args) => {
      calls.push(args);
      return { ok: true, status: 0, stdout: '{"available":true,"machines":[]}', stderr: '', error: '' };
    },
  });

  assert.deepStrictEqual(calls, [[
    'agent-loop', ['inspect', '--json', '--dir', '/project'],
    { cwd: '/project', timeoutMs: 15000 },
  ]]);
  assert.deepStrictEqual(snapshot, { available: true, machines: [] });
});

test('agent-loopを利用できない場合は編集を妨げない縮退情報を返す', async () => {
  const snapshot = await loop.inspect({
    root: '/project',
    capture: async () => ({ ok: false, status: -1, stdout: '', stderr: '', error: 'command not found' }),
  });

  assert.deepStrictEqual(snapshot, {
    available: false,
    machines: [],
    history: [],
    daemon: { running: false },
    error: '実行基盤に接続できませんでした',
  });
});

test('定期実行をJSON標準入力でagent-loopへ保存する', async () => {
  const calls = [];
  const payload = {
    workflow: '.statemachine/review/workflow.yaml', enabled: true,
    schedule: { kind: 'daily', time: '09:30' }, input: { topic: '差分' },
  };
  const result = await loop.saveSchedule({
    root: '/project', payload,
    capture: async (...args) => {
      calls.push(args);
      return { ok: true, status: 0, stdout: '{"saved":true,"applied":false}', stderr: '', error: '' };
    },
  });

  assert.deepStrictEqual(calls, [[
    'agent-loop', ['schedule', '--json', '--dir', '/project'],
    { cwd: '/project', timeoutMs: 15000, input: JSON.stringify(payload) },
  ]]);
  assert.deepStrictEqual(result, { saved: true, applied: false });
});

test('実行終了時は最後のRESULT行を画面用の結果として解釈する', () => {
  assert.deepStrictEqual(
    loop.parseResult('途中の出力\nRESULT {"ok":true,"final_state":"done"}\n', 0),
    { ok: true, final_state: 'done' },
  );
  assert.deepStrictEqual(
    loop.parseResult('RESULT {"ok":false,"escalate":true,"error":"確認が必要"}\n', 3),
    { ok: false, escalate: true, error: '確認が必要' },
  );
  assert.deepStrictEqual(
    loop.parseResult('途中で終了しました', 1),
    { ok: false, error: '実行結果を確認できませんでした（終了コード 1）' },
  );
});

test('自動実行の開始と停止はagent-loopのライフサイクルへ渡す', async () => {
  const starts = [];
  const captures = [];
  assert.deepStrictEqual(await loop.startDaemon({
    root: '/project',
    startDetached: async (...args) => { starts.push(args); return { pid: 123 }; },
  }), { pid: 123 });
  assert.deepStrictEqual(await loop.stopDaemon({
    root: '/project',
    capture: async (...args) => { captures.push(args); return { ok: true, status: 0, stdout: '', stderr: '', error: '' }; },
  }), { stopped: true });

  assert.deepStrictEqual(starts, [[
    'agent-loop', ['--no-auto-attach'], { cwd: '/project' },
  ]]);
  assert.deepStrictEqual(captures, [[
    'agent-loop', ['drain'], { cwd: '/project', timeoutMs: 15000 },
  ]]);
});

test('履歴ログは実行IDだけをagent-loopへ渡して読む', async () => {
  const calls = [];
  const identity = { workflow: '.statemachine/review/workflow.yaml', runId: 'run-1' };
  const result = await loop.readLog({
    root: '/project', identity,
    capture: async (...args) => {
      calls.push(args);
      return { ok: true, status: 0, stdout: '{"text":"done","truncated":false}', stderr: '', error: '' };
    },
  });

  assert.deepStrictEqual(calls, [[
    'agent-loop', ['log', '--json', '--dir', '/project'],
    { cwd: '/project', timeoutMs: 15000, input: JSON.stringify(identity) },
  ]]);
  assert.deepStrictEqual(result, { text: 'done', truncated: false });
});

test('実行基盤との境界は不正な識別名と壊れた応答を明確に拒否する', async () => {
  assert.throws(() => loop.runSpec({ root: '/project', machine: '../outside' }), /識別名/);
  assert.deepStrictEqual(loop.runSpec({
    root: '/project', machine: 'one', parameters: { omitted: null },
  }).args, ['statemachine', '--workflow', '.statemachine/one/workflow.yaml', '--dir', '/project']);
  await assert.rejects(() => loop.inspect({
    root: '/project', capture: async () => ({ ok: true, stdout: 'not-json' }),
  }), /実行情報の応答/);
  await assert.rejects(() => loop.saveSchedule({
    root: '/project', payload: {}, capture: async () => ({ ok: true, stdout: 'not-json' }),
  }), /保存結果の応答/);
  await assert.rejects(() => loop.saveSchedule({
    root: '/project', payload: {}, capture: async () => ({ ok: false, stdout: '{"error":"時刻が不正"}' }),
  }), /時刻が不正/);
  assert.deepStrictEqual(loop.parseResult('RESULT []', null), {
    ok: false, error: '実行結果を確認できませんでした（終了コード ?）',
  });
  await assert.rejects(() => loop.stopDaemon({
    root: '/project', capture: async () => ({ ok: false, error: '停止失敗' }),
  }), /停止失敗/);
  await assert.rejects(() => loop.readLog({
    root: '/project', identity: {}, capture: async () => ({ ok: true, stdout: 'not-json' }),
  }), /ログの応答/);
  await assert.rejects(() => loop.readLog({
    root: '/project', identity: {}, capture: async () => ({ ok: false, stdout: '{"error":"ログなし"}' }),
  }), /ログなし/);
});

test('実行基盤の省略値とエラー応答にも安全な既定を使う', async () => {
  for (const machine of ['', '.', '..']) assert.throws(() => loop.runSpec({ machine }), /識別名/);
  assert.deepStrictEqual(await loop.inspect({
    capture: async (_command, _args, options) => {
      assert.strictEqual(options.cwd, '');
      return { ok: false };
    },
  }), {
    available: false, machines: [], history: [], daemon: { running: false },
    error: '実行基盤に接続できませんでした',
  });
  assert.deepStrictEqual(await loop.saveSchedule({
    payload: {}, capture: async () => ({ ok: true, stdout: '' }),
  }), {});
  await assert.rejects(() => loop.saveSchedule({
    payload: {}, capture: async () => ({ ok: false, stdout: '{}', stderr: '保存失敗' }),
  }), /保存失敗/);
  assert.deepStrictEqual(loop.parseResult(undefined, 0), {
    ok: false, error: '実行結果を確認できませんでした（終了コード 0）',
  });
  assert.deepStrictEqual(await loop.startDaemon({
    startDetached: async (_command, _args, options) => ({ pid: options.cwd === '' ? 1 : 0 }),
  }), { pid: 1 });
  await assert.rejects(() => loop.stopDaemon({
    capture: async () => ({ ok: false }),
  }), /自動実行を停止できませんでした/);
  await assert.rejects(() => loop.readLog({
    identity: {}, capture: async () => ({ ok: false, stdout: '{}', stderr: '読取失敗' }),
  }), /読取失敗/);
});
