'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createGate } = require('../src/main/executionGate');

test('同時実行上限を超える取得を拒否し、解放後は再取得できる', () => {
  const gate = createGate();
  assert.deepStrictEqual(gate.acquire('a', 1), { active: 1, limit: 1 });
  assert.throws(() => gate.acquire('a', 99), (error) => error.code === 'TURN_RUNNING');
  assert.throws(() => gate.acquire('b', 1), (error) => error.code === 'CONCURRENCY_LIMIT');
  assert.deepStrictEqual(gate.snapshot('invalid'), { active: 1, limit: 2, ids: ['a'] });
  assert.deepStrictEqual(gate.release('a', 1), { active: 0, limit: 1 });
  assert.deepStrictEqual(gate.acquire('b', 1), { active: 1, limit: 1 });
  gate.release('b', 1);
});
