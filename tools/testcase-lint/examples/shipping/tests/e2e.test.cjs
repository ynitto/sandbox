const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');

test('checkout/free-shipping', () => {
  const result = spawnSync(process.env.PYTHON || 'python3', ['shipping.py', '5000'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), '0');
});
