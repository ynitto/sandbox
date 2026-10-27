import assert from 'node:assert/strict';
import { test } from 'node:test';
import { coverCorrection, coverScale, fitMatrix, hullInImage, isCovered } from '../src/core/cover.js';
import { drawnImageRect } from '../src/core/geometry.js';
import {
  DEFAULT_MOTION, elasticView, lerpSimilarity, momentumStep, rawFromView, resolveMotion, rubber, rubberInverse, springAt,
} from '../src/core/motion.js';
import { about, compose, equals, IDENTITY, multiply, rotate, scale, scaleOf, translate } from '../src/core/matrix.js';
import { parseClipDocument } from '../src/core/parse.js';
import { doc, env } from './helpers.js';


// 枠: (10,10)-(90,70)、画像: 0..200 × 0..100（恒等で枠を覆う）
const slotOf = (clip) => parseClipDocument(doc(clip ? { clip } : {}), env).model.slots[0];
const slot = slotOf();
const D = drawnImageRect(slot.image);

test('覆っているときは補正しない', () => {
  const c = coverCorrection(slot, IDENTITY, D);
  assert.equal(c.k, 1);
  assert.deepEqual(c.u, [0, 0]);
  assert.ok(isCovered(slot, IDENTITY, D));
});

test('ずらしすぎ・縮めすぎ・回転を補正すると枠の全頂点が D に入る', () => {
  const cases = [
    translate(50, 0),
    translate(-150, -60),
    multiply(about([50, 40], scale(0.3)), IDENTITY),
    multiply(about([50, 40], rotate(40)), IDENTITY),
    compose(translate(30, 30), about([50, 40], compose(rotate(-70), scale(0.4)))),
  ];
  for (const M of cases) {
    assert.ok(!isCovered(slot, M, D));
    const fixed = coverCorrection(slot, M, D).M;
    assert.ok(isCovered(slot, fixed, D, 1e-9), `${M}`);
    const q = hullInImage(slot, fixed);
    assert.ok(q.x >= D.x - 1e-9 && q.y >= D.y - 1e-9 && q.x + q.width <= D.x + D.width + 1e-9 && q.y + q.height <= D.y + D.height + 1e-9);
  }
});

test('補正の拡大率は、二分探索で求めた覆える最小の拡大率と一致する', () => {
  const circle = slotOf('<circle cx="50" cy="40" r="30"/>');
  const M = multiply(about([50, 40], compose(rotate(33), scale(0.2))), IDENTITY);
  const k = coverCorrection(circle, M, D).k;
  // 拡大率 s で、平行移動を最適にとったとき覆えるか
  const coverable = (s) => {
    const Ms = multiply(about([50, 40], scale(s)), M);
    return isCovered(circle, coverCorrection(circle, Ms, D).M, D) && coverScale(circle, Ms, D) <= 1 + 1e-12;
  };
  let lo = 1;
  let hi = 100;
  for (let i = 0; i < 80; i++) {
    const mid = (lo + hi) / 2;
    if (coverable(mid)) hi = mid;
    else lo = mid;
  }
  assert.ok(Math.abs(k - hi) < 1e-6, `${k} ≠ ${hi}`);
});

test('枠に合わせる: 丁度覆う大きさ（縮めることもある）で中央に置く', () => {
  const M = fitMatrix(slot, compose(scale(3)), D);
  assert.ok(isCovered(slot, M, D, 1e-9));
  assert.ok(Math.abs(coverScale(slot, M, D) - 1) < 1e-9);
});

test('ゴムの関数と逆関数', () => {
  for (const d of [0.1, 1, 10, 1000]) {
    const r = rubber(d, 20, 0.55);
    assert.ok(r > 0 && r < 20);
    assert.ok(Math.abs(rubberInverse(r, 20, 0.55) - d) < 1e-6 * Math.max(1, d));
  }
  assert.ok(rubber(2, 20, 0.55) > rubber(1, 20, 0.55));
  assert.equal(rubber(5, 20, 0), 0);
  assert.equal(rubber(5, 0, 0.55), 0);
});

test('越えていなければ見せる行列は生の行列と同じ', () => {
  const v = elasticView(slot, translate(-5, -5), D, DEFAULT_MOTION);
  assert.ok(equals(v.view, translate(-5, -5)));
  assert.equal(v.over, false);
});

test('越えた分は縮んで見え、上限を超えない。逆にたどると生の位置に戻る', () => {
  let prev = 0;
  for (const dx of [20, 40, 80, 160, 1000]) {
    const raw = translate(dx, 0); // 左端が枠の左（10）を越える
    const { view, over } = elasticView(slot, raw, D, DEFAULT_MOTION);
    assert.ok(over);
    const shown = view[4] - 10; // 見せている越え量
    assert.ok(shown > prev && shown < dx - 10);
    assert.ok(shown <= 60 * DEFAULT_MOTION.rubberLimit + 1e-9);
    prev = shown;
    assert.ok(equals(rawFromView(slot, view, D, DEFAULT_MOTION), raw, 1e-6));
  }
});

test('定数がすべて 0 なら端で止まる（越えない）', () => {
  const { motion } = resolveMotion(Object.fromEntries(Object.keys(DEFAULT_MOTION).map((k) => [k, 0])));
  const { view, raw } = elasticView(slot, translate(100, 0), D, motion);
  assert.ok(equals(view, translate(10, 0)));
  assert.ok(equals(raw, view));
  const shrink = elasticView(slot, about([50, 40], scale(0.2)), D, motion);
  assert.ok(isCovered(slot, shrink.view, D, 1e-9));
});

test('定数の指定: 負や数でない値は既定に戻し、reducedMotion は時間を 0 にする', () => {
  const { motion, warnings } = resolveMotion({ rubber: -1, friction: 'x', springPeriod: 0.2, nope: 1 });
  assert.equal(motion.rubber, DEFAULT_MOTION.rubber);
  assert.equal(motion.friction, DEFAULT_MOTION.friction);
  assert.equal(motion.springPeriod, 0.2);
  assert.equal(warnings.length, 3);
  const reduced = resolveMotion({}, { reducedMotion: true }).motion;
  assert.equal(reduced.friction, 0);
  assert.equal(reduced.springPeriod, 0);
  assert.equal(reduced.snapDuration, 0);
  assert.equal(reduced.rubber, DEFAULT_MOTION.rubber);
});

test('ばねは目標に収束し、周期 0 なら即座', () => {
  assert.ok(Math.abs(springAt(10, 0, 0.4, 2).x) < 1e-6);
  assert.deepEqual(springAt(10, 5, 0, 0), { x: 0, v: 0 });
  const mid = springAt(10, 0, 0.4, 0.1).x;
  assert.ok(mid > 0 && mid < 10); // 臨界減衰: 行き過ぎない
});

test('慣性は摩擦で減速し、摩擦 0 なら止まる', () => {
  const a = momentumStep(IDENTITY, [100, 0], 0.016, 0.325);
  assert.ok(a.M[4] > 1.5 && a.v[0] < 100);
  assert.deepEqual(momentumStep(IDENTITY, [100, 0], 0.016, 0).v, [0, 0]);
});

test('相似変換の補間は途中も相似変換で、拡大率は比で動く', () => {
  const a = IDENTITY;
  const b = compose(rotate(90), scale(2));
  const m = lerpSimilarity(a, b, 0.5, [100, 50]);
  assert.ok(Math.abs(scaleOf(m) - Math.SQRT2) < 1e-9);
  assert.ok(Math.abs(m[0] - m[3]) < 1e-9 && Math.abs(m[1] + m[2]) < 1e-9);
});
