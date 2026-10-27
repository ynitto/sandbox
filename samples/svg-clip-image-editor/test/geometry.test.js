import assert from 'node:assert/strict';
import { test } from 'node:test';
import { apply, IDENTITY, parseTransform } from '../src/core/matrix.js';
import { clipHull, clipSegments, convexHull, drawnImageRect } from '../src/core/geometry.js';
import { parsePathData, shapeToPath } from '../src/core/path.js';

// 点が凸多角形の内側（境界を含む）にあるか
function inside(poly, p, eps = 1e-9) {
  let sign = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    if (Math.abs(c) <= eps) continue;
    if (sign === 0) sign = Math.sign(c);
    else if (Math.sign(c) !== sign) return false;
  }
  return true;
}

// 区分の上の点を細かく標本する（真の図形の輪郭）
function sample(segs, n = 64) {
  const pts = [];
  for (const s of segs) {
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      if (s.type === 'L') pts.push([s.p[0][0] + (s.p[1][0] - s.p[0][0]) * t, s.p[0][1] + (s.p[1][1] - s.p[0][1]) * t]);
      else {
        const [a, b, c, d] = s.p;
        const u = 1 - t;
        pts.push([0, 1].map((k) => u ** 3 * a[k] + 3 * u * u * t * b[k] + 3 * u * t * t * c[k] + t ** 3 * d[k]));
      }
    }
  }
  return pts;
}

// 点と多角形の輪郭の距離
function distToPolyline(pts, p) {
  let best = Infinity;
  for (const q of pts) best = Math.min(best, Math.hypot(q[0] - p[0], q[1] - p[1]));
  return best;
}

test('パス: 全コマンドの相対・絶対と、詰めて書いた円弧のフラグ', () => {
  const types = parsePathData('M10 10 h5 v5 H20 V30 L0 0 l1 1 C1 2 3 4 5 6 c1 1 2 2 3 3 S9 9 10 10 s1 1 2 2 Q1 1 2 2 q1 1 2 2 T5 5 t1 1 A5 5 0 0 1 30 30 a5 5 0 01 10 0 Z')
    .map((s) => s.type).join('');
  // h v H V L l の 6 本が直線、C〜a はすべてベジェ、最後の Z が閉じる直線
  assert.match(types, /^LLLLLLC{10,}L$/);
  assert.throws(() => parsePathData('M0 0 X1'));
  assert.throws(() => parsePathData('10 10'));
  // 退化した円弧: 半径 0 は直線、端点一致は何もない
  assert.deepEqual(parsePathData('M0 0 A0 5 0 0 1 10 0').map((s) => s.type), ['L']);
  assert.equal(parsePathData('M0 0 A5 5 0 0 1 0 0').length, 0);
  // 暗黙の繰り返し（M の後の座標は L）
  assert.equal(parsePathData('M0 0 10 0 10 10').length, 2);
});

const shapes = {
  rect: [{ tag: 'rect', attrs: { x: '10', y: '20', width: '100', height: '50' }, transform: IDENTITY }],
  rounded: [{ tag: 'rect', attrs: { x: '0', y: '0', width: '100', height: '60', rx: '20' }, transform: IDENTITY }],
  circle: [{ tag: 'circle', attrs: { cx: '50', cy: '50', r: '40' }, transform: IDENTITY }],
  ellipse: [{ tag: 'ellipse', attrs: { cx: '0', cy: '0', rx: '60', ry: '20' }, transform: parseTransform('rotate(30)') }],
  concave: [{ tag: 'polygon', attrs: { points: '0,0 100,0 100,100 50,40 0,100' }, transform: IDENTITY }],
  holeEvenodd: [{ tag: 'path', attrs: { d: 'M0 0H100V100H0Z M30 30H70V70H30Z', 'clip-rule': 'evenodd' }, transform: IDENTITY }],
  curves: [{ tag: 'path', attrs: { d: 'M0 50 C0 0 100 0 100 50 Q50 120 0 50Z' }, transform: IDENTITY }],
  arc: [{ tag: 'path', attrs: { d: 'M10 50 A40 25 20 1 1 90 50 Z' }, transform: IDENTITY }],
  multi: [
    { tag: 'circle', attrs: { cx: '0', cy: '0', r: '10' }, transform: IDENTITY },
    { tag: 'rect', attrs: { x: '50', y: '50', width: '10', height: '10' }, transform: parseTransform('rotate(45 55 55)') },
  ],
};

for (const [name, list] of Object.entries(shapes)) {
  test(`凸包（${name}）: 図形を必ず含み、はみ出しは ε 以内`, () => {
    const Q = parseTransform('translate(3 4) scale(1.5)');
    const eps = 0.05;
    const hull = clipHull(list, Q, eps);
    const truth = sample(clipSegments(list, Q));
    for (const p of truth) assert.ok(inside(hull, p, 1e-7), `${name}: ${p} が凸包の外`);
    const truthHull = convexHull(truth);
    for (const v of hull) assert.ok(distToPolyline(sample(truthHull.map((a, i) => ({ type: 'L', p: [a, truthHull[(i + 1) % truthHull.length]] })), 50), v) <= eps * 1.5 + 0.02, `${name}: 頂点 ${v} が離れすぎ`);
  });
}

test('凹みは凸包に影響しない（設計書の例の枠）', () => {
  const hull = clipHull([{ tag: 'path', attrs: { d: 'M200 150 H600 V450 H420 Q400 380 380 450 H200 Z' }, transform: IDENTITY }], IDENTITY);
  assert.deepEqual(hull.map((p) => p.map(Math.round)).sort(), [[200, 150], [200, 450], [600, 150], [600, 450]].sort());
});

test('描画矩形: none / slice は矩形そのもの、meet は内側', () => {
  const im = { x: 0, y: 0, width: 200, height: 100 };
  assert.deepEqual(drawnImageRect({ ...im, preserveAspectRatio: 'none' }, { width: 10, height: 10 }), im);
  assert.deepEqual(drawnImageRect({ ...im, preserveAspectRatio: 'xMidYMid slice' }, { width: 10, height: 10 }), im);
  assert.deepEqual(drawnImageRect({ ...im, preserveAspectRatio: 'xMidYMid meet' }, { width: 10, height: 10 }), { x: 50, y: 0, width: 100, height: 100 });
  assert.deepEqual(drawnImageRect({ ...im, preserveAspectRatio: 'xMinYMin' }, { width: 10, height: 10 }), { x: 0, y: 0, width: 100, height: 100 });
  // 元の大きさが分からないうちは矩形そのもの
  assert.deepEqual(drawnImageRect({ ...im, preserveAspectRatio: 'xMidYMid meet' }), im);
});

test('基本図形のパス', () => {
  assert.equal(shapeToPath('rect', { width: '0', height: '5' }), '');
  assert.ok(shapeToPath('polyline', { points: '0 0 10 0 10 10' }).endsWith('Z'));
  const pts = parsePathData(shapeToPath('circle', { cx: '0', cy: '0', r: '1' })).flatMap((s) => [s.p[0], s.p[s.p.length - 1]]);
  for (const p of pts) assert.ok(Math.abs(Math.hypot(...p) - 1) < 1e-9);
  assert.ok(apply(IDENTITY, [1, 2])[1] === 2);
});
