'use strict';

const assert = require('assert');
require('../src/renderer/routine-ui-cache');
const { createBoundedAsyncCache } = globalThis.RoutineUiCache;

async function run() {
  let calls = 0;
  const cache = createBoundedAsyncCache({ max: 3, ttlMs: 30000 });
  const loader = async () => ({ items: [++calls] });

  const first = await cache.load('repo-a', loader);
  const second = await cache.load('repo-a', loader);

  assert.deepStrictEqual(first, { items: [1] });
  assert.strictEqual(second, first, '同じキーの有効なスナップショットを再利用する');
  assert.strictEqual(calls, 1, '選択を戻しても全体データを再取得しない');

  assert.strictEqual(cache.get('missing'), undefined);
  cache.set('repo-b', { items: [2] });
  assert.deepStrictEqual(cache.get('repo-b'), { items: [2] });
  cache.set('repo-c', { items: [3] });
  cache.set('repo-d', { items: [4] });
  assert.strictEqual(cache.get('repo-a'), undefined, '上限を超えた古い詳細を解放する');
  assert.strictEqual(cache.size, 3);

  cache.set('expired', { old: true }, 1);
  assert.strictEqual(cache.peek('expired', 40000), undefined, '期限切れの値を再利用しない');
  assert.strictEqual(cache.peek('missing'), undefined);

  let release;
  let concurrentCalls = 0;
  const pending = cache.load('shared', () => {
    concurrentCalls += 1;
    return new Promise((resolve) => { release = resolve; });
  });
  const shared = cache.load('shared', () => {
    concurrentCalls += 1;
    return Promise.resolve('wrong');
  });
  await Promise.resolve();
  release('shared-value');
  assert.deepStrictEqual(await Promise.all([pending, shared]), ['shared-value', 'shared-value']);
  assert.strictEqual(concurrentCalls, 1, '同一キーの並行取得を1回へまとめる');

  let releaseStale;
  const stale = cache.load('stale', () => new Promise((resolve) => { releaseStale = resolve; }));
  await Promise.resolve();
  cache.delete('stale');
  releaseStale('old-response');
  await stale;
  assert.strictEqual(cache.get('stale'), undefined, '無効化後に届いた古い応答を再登録しない');

  let forced = 0;
  await cache.load('force', async () => ++forced);
  await cache.load('force', async () => ++forced, { force: true });
  assert.strictEqual(forced, 2, '手動更新では有効な値も更新する');
  assert.strictEqual(cache.delete('force'), true);
  assert.strictEqual(cache.get('force'), undefined);
  cache.clear();
  assert.strictEqual(cache.size, 0);

  const defaults = createBoundedAsyncCache();
  defaults.set('x', 1);
  assert.strictEqual(defaults.peek('x'), 1);
  console.log('routine-ui-cache: all tests passed');
}

run().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
