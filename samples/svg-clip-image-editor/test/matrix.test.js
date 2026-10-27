import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  about, apply, compose, decompose, equals, formatDecomposed, formatMatrix, invert, multiply,
  parseTransform, rotate, scale, translate,
} from '../src/core/matrix.js';

const close = (a, b, eps = 1e-9) => assert.ok(equals(a, b, eps), `${a} ≠ ${b}`);

test('transform の各関数と合成順（左から右へ掛ける）', () => {
  close(parseTransform('translate(10 20) scale(2)'), compose(translate(10, 20), scale(2)));
  close(parseTransform('rotate(90 10 0)'), about([10, 0], rotate(90)));
  close(parseTransform('matrix(1,2,3,4,5,6)'), [1, 2, 3, 4, 5, 6]);
  close(parseTransform('skewX(45)'), [1, 0, 1, 1, 0, 0]);
  close(parseTransform(''), [1, 0, 0, 1, 0, 0]);
  assert.equal(parseTransform('translate(1 2'), null);
  assert.equal(parseTransform('foo(1)'), null);
  assert.equal(parseTransform('rotate(1 2)'), null);
});

test('逆行列と適用', () => {
  const m = parseTransform('translate(5 -3) rotate(30) scale(2 0.5)');
  close(multiply(m, invert(m)), [1, 0, 0, 1, 0, 0]);
  const p = apply(m, [3, 4]);
  close(apply(invert(m), p).concat([0, 0, 0, 0]), [3, 4, 0, 0, 0, 0]);
  assert.equal(invert([0, 0, 0, 1, 0, 0]), null);
});

test('分解と書式の往復', () => {
  const m = compose(translate(12.5, -3), rotate(-20), scale(1.5));
  const d = decompose(m);
  assert.ok(Math.abs(d.rotate + 20) < 1e-9);
  close(parseTransform(formatDecomposed(m, 6)), m, 1e-5);
  close(parseTransform(formatMatrix(m, 6)), m, 1e-5);
  assert.equal(decompose(parseTransform('skewX(10)')), null);
  assert.equal(formatMatrix([1, 0, 0, 1, -0.0000001, 2.5]), 'matrix(1 0 0 1 0 2.5)');
});
