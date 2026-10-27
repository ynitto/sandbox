import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EditorEngine, ROTATION_SNAP_DEG } from '../src/react/engine.js';
import { drawnImageRect, focusRect } from '../src/core/geometry.js';
import { isCovered } from '../src/core/cover.js';
import { DEFAULT_MOTION, resolveMotion } from '../src/core/motion.js';
import { equals, rotationOf, scaleOf, translate } from '../src/core/matrix.js';
import { parseClipDocument } from '../src/core/parse.js';
import { doc, env } from './helpers.js';

const ZERO = Object.fromEntries(Object.keys(DEFAULT_MOTION).map((k) => [k, 0]));

// 偽の時計: frame() で 16ms 進めて requestAnimationFrame を回す
function fakeHost() {
  let now = 0;
  let nextId = 1;
  const frames = new Map();
  const timers = new Map();
  const host = {
    renders: [],
    commits: [],
    render(M) {
      host.renders.push(M);
    },
    commit(name, M, all) {
      host.commits.push({ name, M, all });
    },
    schedule(fn) {
      const id = nextId++;
      frames.set(id, fn);
      return id;
    },
    cancel(id) {
      frames.delete(id);
    },
    setTimer(fn, ms) {
      const id = nextId++;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
    now: () => now,
    pending: () => frames.size + timers.size,
    frame(ms = 16) {
      now += ms;
      for (const [id, t] of [...timers]) {
        if (t.at <= now) {
          timers.delete(id);
          t.fn();
        }
      }
      const due = [...frames];
      frames.clear();
      for (const [, fn] of due) fn(now);
    },
    runUntilIdle(limit = 2000) {
      let n = 0;
      while (host.pending() && n++ < limit) host.frame();
      return n;
    },
  };
  return host;
}

// 枠 (10,10)-(90,70)、画像 0..200 × 0..100
function setup({ motion = {}, rotationCover = 'rubber', svg = doc() } = {}) {
  const model = parseClipDocument(svg, env).model;
  const host = fakeHost();
  const engine = new EditorEngine(host);
  const ctxFor = (slot) => ({
    model,
    slot,
    D: drawnImageRect(slot.image),
    motion: resolveMotion(motion).motion,
    rotationCover,
    minScale: 0.1,
    maxScale: 20,
    focusWidthC: focusRect(slot).width,
    pxPerC: () => 4,
  });
  engine.update(ctxFor(model.slots[0]));
  return { engine, host, model, ctxFor, slot: model.slots[0], D: drawnImageRect(model.slots[0].image) };
}

const drag = (engine, host, path, { id = 1, ms = 16 } = {}) => {
  engine.pointerDown(id, path[0], host.now());
  for (const p of path.slice(1)) {
    host.frame(ms);
    engine.pointerMove(id, p, host.now());
  }
};

test('すべて 0: 端に当たった瞬間に止まり、指を戻すとすぐ動き、離すと即座に確定する', () => {
  const { engine, host } = setup({ motion: ZERO });
  drag(engine, host, [[50, 40], [100, 40], [200, 40]]);
  assert.ok(equals(engine.view, translate(10, 0)), `${engine.view}`);
  host.frame();
  engine.pointerMove(1, [190, 40], host.now()); // 10 戻す
  assert.ok(equals(engine.view, translate(0, 0)), `遊びがある: ${engine.view}`);
  engine.pointerUp(1, host.now());
  assert.equal(host.pending(), 0, 'アニメーションが残っている');
  assert.equal(host.commits.length, 0, '動いていないので確定はない（0 に戻った）');
  // 端で止めたまま離すと、その位置で即座に確定する
  drag(engine, host, [[50, 40], [200, 40]]);
  engine.pointerUp(1, host.now());
  assert.equal(host.pending(), 0);
  assert.equal(host.commits.length, 1);
  assert.ok(equals(host.commits[0].M, translate(10, 0)));
});

test('既定: 端を越えて引くとゴムで遅れ、離すとばねで覆える位置へ戻って確定する', () => {
  const { engine, host, slot, D } = setup();
  drag(engine, host, [[50, 40], [100, 40], [150, 40]]);
  const shown = engine.view[4];
  assert.ok(shown > 10 && shown < 100, `${shown}`);
  host.frame(300); // 指を止めてから離す（慣性なし）
  engine.pointerUp(1, host.now());
  assert.ok(host.pending() > 0, 'ばねが動いていない');
  host.runUntilIdle();
  assert.equal(host.commits.length, 1);
  assert.ok(isCovered(slot, host.commits[0].M, D, 1e-6));
  assert.ok(Math.abs(host.commits[0].M[4] - 10) < 1e-6);
});

test('弾くと慣性で滑り、端に当たるとばねで戻って止まる', () => {
  const { engine, host, slot, D } = setup();
  const path = Array.from({ length: 6 }, (_, i) => [50 + i * 4, 40]); // 16ms ごとに 4 → 250/秒
  drag(engine, host, path);
  engine.pointerUp(1, host.now());
  assert.equal(engine.anim?.mode, 'momentum');
  const frames = host.runUntilIdle();
  assert.ok(frames > 5);
  assert.equal(host.commits.length, 1);
  const M = host.commits[0].M;
  assert.ok(M[4] > 20 - 1e-6 || Math.abs(M[4] - 10) < 1e-6);
  assert.ok(isCovered(slot, M, D, 1e-6));
});

test('慣性・ばねの途中で掴むと、見えている位置から始まって跳ねない', () => {
  const { engine, host } = setup();
  drag(engine, host, [[50, 40], [150, 40]]);
  host.frame(300);
  engine.pointerUp(1, host.now());
  host.frame();
  host.frame();
  const before = engine.view.slice();
  engine.pointerDown(2, [60, 40], host.now());
  host.frame();
  engine.pointerMove(2, [60, 40], host.now()); // 動かさない
  assert.ok(equals(engine.view, before, 1e-6), `${before} → ${engine.view}`);
});

// 画像が枠と同じ大きさなら、回転すると必ず拡大が要る
const TIGHT = doc().replace('width="200" height="100"', 'x="10" y="10" width="80" height="60"');

test('2 本指: 拡縮と回転。rotationCover=grow なら回転中も常に覆う', () => {
  for (const rotationCover of ['rubber', 'grow']) {
    const { engine, host, slot, D } = setup({ rotationCover, svg: TIGHT });
    engine.pointerDown(1, [30, 40], 0);
    engine.pointerDown(2, [70, 40], 0);
    let uncovered = 0;
    for (let deg = 3; deg <= 45; deg += 3) {
      const r = (deg * Math.PI) / 180;
      host.frame();
      engine.pointerMove(1, [50 - 20 * Math.cos(r), 40 - 20 * Math.sin(r)], host.now());
      engine.pointerMove(2, [50 + 20 * Math.cos(r), 40 + 20 * Math.sin(r)], host.now());
      // 2 本とも動かした後（枠の中心まわりの回転だけになった状態）で確かめる
      if (!isCovered(slot, engine.view, D, 1e-6)) uncovered++;
    }
    assert.ok(Math.abs(rotationOf(engine.view) - 45) < 1e-6);
    if (rotationCover === 'grow') assert.equal(uncovered, 0);
    else assert.ok(uncovered > 0, 'rubber では回転中に覆えない時間がある');
    engine.pointerUp(1, host.now());
    engine.pointerUp(2, host.now());
    host.runUntilIdle();
    assert.ok(isCovered(slot, engine.view, D, 1e-6));
  }
});

test('回転を戻すと grow の拡大も元に戻る', () => {
  const { engine, host } = setup({ rotationCover: 'grow', svg: TIGHT });
  engine.pointerDown(1, [30, 40], 0);
  engine.pointerDown(2, [70, 40], 0);
  const r = (40 * Math.PI) / 180;
  engine.pointerMove(1, [50 - 20 * Math.cos(r), 40 - 20 * Math.sin(r)], host.now());
  engine.pointerMove(2, [50 + 20 * Math.cos(r), 40 + 20 * Math.sin(r)], host.now());
  const grown = scaleOf(engine.view);
  assert.ok(grown > 1.01, `${grown}`);
  engine.pointerMove(1, [30, 40], host.now());
  engine.pointerMove(2, [70, 40], host.now());
  assert.ok(Math.abs(scaleOf(engine.view) - 1) < 1e-9);
});

test('ボタン: 回転は覆う大きさまで拡大し、元に戻す・やり直すで行き来する', () => {
  const { engine, host, slot, D } = setup();
  engine.rotateBy(90);
  host.runUntilIdle();
  assert.equal(host.commits.length, 1);
  assert.ok(isCovered(slot, engine.view, D, 1e-6));
  assert.ok(Math.abs(rotationOf(engine.view) - 90) < 1e-6);
  engine.undo();
  host.runUntilIdle();
  assert.ok(equals(engine.view, slot.M0, 1e-9));
  assert.ok(engine.canRedo());
  engine.redo();
  host.runUntilIdle();
  assert.ok(Math.abs(rotationOf(engine.view) - 90) < 1e-6);
});

test('2 本指の回転は 90° の倍数の近くで吸い付き、キーの回転は吸い付かない', () => {
  const twist = (deg) => {
    const { engine, host } = setup();
    engine.pointerDown(1, [30, 40], 0);
    engine.pointerDown(2, [70, 40], 0);
    const r = (deg * Math.PI) / 180;
    engine.pointerMove(1, [50 - 20 * Math.cos(r), 40 - 20 * Math.sin(r)], host.now());
    engine.pointerMove(2, [50 + 20 * Math.cos(r), 40 + 20 * Math.sin(r)], host.now());
    return rotationOf(engine.view);
  };
  assert.ok(Math.abs(twist(ROTATION_SNAP_DEG * 0.75)) < 1e-9, '指のわずかなねじれで傾いた');
  assert.ok(Math.abs(twist(-ROTATION_SNAP_DEG * 0.75)) < 1e-9);
  assert.ok(Math.abs(twist(ROTATION_SNAP_DEG * 2) - ROTATION_SNAP_DEG * 2) < 1e-9);
  assert.ok(Math.abs(twist(89) - 90) < 1e-9);
  const { engine, host } = setup({ motion: { snapDuration: 0 } });
  engine.rotateBy(1);
  host.runUntilIdle();
  assert.ok(Math.abs(rotationOf(engine.view) - 1) < 1e-9);
});

test('ホイール: 止まってから戻し、確定する', () => {
  const { engine, host, slot, D } = setup();
  for (let i = 0; i < 5; i++) engine.wheel([50, 40], 100, false); // 縮小
  assert.ok(!isCovered(slot, engine.view, D));
  host.runUntilIdle();
  assert.equal(host.commits.length, 1);
  assert.ok(isCovered(slot, engine.view, D, 1e-6));
});

test('スロットの切り替え: 動いているものを確定してから切り替え、値と履歴を保つ', () => {
  const svg = doc().replace('</svg>', '<g data-clip-slot="b" clip-path="url(#c)"><image href="b.png" width="300" height="300"/></g></svg>');
  const { engine, host, model, ctxFor } = setup({ svg });
  drag(engine, host, [[50, 40], [60, 40]]);
  engine.update(ctxFor(model.slots[1]));
  assert.equal(host.commits.length, 1);
  assert.equal(host.commits[0].name, 'a');
  assert.ok(equals(host.commits[0].M, translate(10, 0)));
  assert.equal(engine.slotName, 'b');
  assert.equal(engine.canUndo(), false);
  engine.update(ctxFor(model.slots[0]));
  assert.ok(equals(engine.view, translate(10, 0)));
  assert.equal(engine.canUndo(), true);
  assert.deepEqual(Object.keys(host.commits[0].all).sort(), ['a', 'b']);
});

test('外に知らせる状態: 元に戻せるか・覆えているか・動いているか', () => {
  const { engine, host } = setup();
  assert.deepEqual(engine.state(), { canUndo: false, canRedo: false, covered: true, moving: false });
  drag(engine, host, [[50, 40], [150, 40]]);
  assert.deepEqual(engine.state(), { canUndo: false, canRedo: false, covered: false, moving: true });
  host.frame(300);
  engine.pointerUp(1, host.now());
  assert.equal(engine.state().moving, true);
  host.runUntilIdle();
  assert.deepEqual(engine.state(), { canUndo: true, canRedo: false, covered: true, moving: false });
});
