'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const createReader = require('../src/main/automation/snapshot-reader');

test('失敗しても同じリポジトリのコマンド一覧・履歴を保持し、復旧時は置き換える', async () => {
  let response = { available: true, tasks: [{ id: 'command', history: [{ runId: 'one' }] }] };
  const read = createReader(async () => { if (response instanceof Error) throw response; return response; });
  await read('/a');
  response = new Error('WSL timeout');
  const failed = await read('/a');
  assert.equal(failed.available, false);
  assert.equal(failed.stale, true);
  assert.equal(failed.tasks[0].history[0].runId, 'one');
  assert.equal(failed.error, 'WSL timeout');
  assert.deepEqual((await read('/b')).tasks, []);
  response = { available: true, tasks: [] };
  assert.deepEqual((await read('/a')).tasks, []);
});

test('親一覧と詳細からの同時取得をまとめる', async () => {
  let finish, calls = 0;
  const read = createReader(() => { calls++; return new Promise((resolve) => { finish = resolve; }); });
  const first = read('/a'), second = read('/a');
  await Promise.resolve();
  assert.equal(calls, 1);
  finish({ available: true, tasks: [] });
  assert.equal(await first, await second);
});
