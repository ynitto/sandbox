// エディタの動き（ジェスチャ・ゴム・慣性・ばね・履歴）。React にも DOM にも依存しない。
// 座標はすべて C 空間（スロットの <g> のユーザー空間）で受け取る。時刻はミリ秒。
//
// host が受け持つもの:
//   render(M, { silent })     見せる行列を画面に出す（silent でなければ onChange も呼ぶ）
//   commit(slotName, M, all)  確定を外に知らせる（all は全スロットの確定値）
//   schedule(fn) / cancel(id) 次のフレームで fn(now) を呼ぶ（requestAnimationFrame）
//   setTimer(fn, ms) / clearTimer(id)
//   now()

import {
  about,
  clipCenter,
  compose,
  coverCorrection,
  coverScale,
  easeOutCubic,
  elasticView,
  equals,
  fitMatrix,
  isCovered,
  lerpSimilarity,
  momentumStep,
  multiply,
  rawFromView,
  rotate,
  rotationOf,
  scale,
  scaleOf,
  springMatrix,
  translate,
} from '../core/index.js';

const ZERO6 = [0, 0, 0, 0, 0, 0];
const HISTORY_LIMIT = 100;
const KEY_MERGE_MS = 500;

export class EditorEngine {
  constructor(host) {
    this.host = host;
    this.ctx = null; // { model, slot, D, motion, cover, rotationCover, minScale, maxScale, rotationSnap, focusWidthC, pxPerC() }
    this.slotName = null;
    this.view = null; // 見せている行列
    this.raw = null; // 生の行列（指に正確に追従する値）
    this.anim = null; // { mode: 'momentum' | 'spring' | 'snap', … }
    this.frame = null;
    this.pointers = new Map(); // id → C 空間の点
    this.gesture = null; // { startM, startPts, multi }
    this.samples = []; // 1 本指の { t, p }（速度を求める）
    this.wheelRaw = null;
    this.wheelTimer = null;
    this.committed = {}; // スロット名 → 確定した行列
    this.history = {}; // スロット名 → { past, future, lastKeyAt }
    this.initial = () => null;
  }

  // ── 文脈の更新・スロットの切り替え ──

  /** 描画のたびに呼ぶ。文書かスロットが変わったら、動いているものを確定してから切り替える */
  update(ctx, initial) {
    const prev = this.ctx;
    this.initial = initial;
    if (!prev || prev.model !== ctx.model) {
      if (prev) this.finish();
      this.committed = {};
      this.history = {};
      this.ctx = ctx;
      this.enter(ctx.slot.name);
      return;
    }
    if (prev.slot.name !== ctx.slot.name) {
      this.finish();
      this.ctx = ctx;
      this.enter(ctx.slot.name);
      return;
    }
    this.ctx = ctx;
  }

  enter(name) {
    this.slotName = name;
    const M = this.committed[name] ?? this.initial(name) ?? this.ctx.slot.M0;
    this.committed[name] = M;
    this.view = M;
    this.raw = M;
    this.host.render(M, { silent: true });
  }

  /** 親が値を持つ（制御モード）とき。止まっている間だけ反映する */
  external(name, M) {
    if (name !== this.slotName || this.busy() || equals(M, this.view, 1e-12)) return;
    this.committed[name] = M;
    this.view = M;
    this.raw = M;
    this.host.render(M, { silent: true });
  }

  busy() {
    return Boolean(this.anim || this.pointers.size || this.wheelTimer);
  }

  /** 全スロットの値（確定値、なければ初期値） */
  allTransforms() {
    const out = {};
    for (const s of this.ctx?.model.slots ?? []) out[s.name] = this.committed[s.name] ?? this.initial(s.name) ?? s.M0;
    return out;
  }

  // ── 画面と確定 ──

  render(M) {
    this.view = M;
    this.host.render(M, { silent: false });
  }

  commit(M, { record = true, mergeKey = false } = {}) {
    const name = this.slotName;
    const prev = this.committed[name];
    this.view = M;
    this.raw = M;
    this.host.render(M, { silent: true });
    if (prev && equals(prev, M, 1e-12)) return;
    if (record) {
      const h = this.hist(name);
      const now = this.host.now();
      const last = h.past[h.past.length - 1];
      if (mergeKey && last && h.lastKeyAt != null && now - h.lastKeyAt < KEY_MERGE_MS) {
        last.after = M;
      } else {
        h.past.push({ before: prev, after: M });
        if (h.past.length > HISTORY_LIMIT) h.past.shift();
      }
      h.future = [];
      h.lastKeyAt = mergeKey ? now : null;
    }
    this.committed[name] = M;
    this.host.commit(name, M, this.allTransforms());
  }

  hist(name) {
    return (this.history[name] ??= { past: [], future: [], lastKeyAt: null });
  }

  canUndo() {
    return this.hist(this.slotName).past.length > 0;
  }

  canRedo() {
    return this.hist(this.slotName).future.length > 0;
  }

  /** 外に知らせる状態: 元に戻せるか・やり直せるか・枠を覆えているか・動いているか */
  state() {
    const { slot, D } = this.ctx;
    return {
      canUndo: this.canUndo(),
      canRedo: this.canRedo(),
      covered: isCovered(slot, this.view, D, 1e-4),
      moving: this.busy(),
    };
  }

  // ── アニメーション ──

  start(anim) {
    this.anim = anim;
    if (this.frame == null) this.frame = this.host.schedule((now) => this.tick(now));
  }

  stopFrame() {
    if (this.frame != null) this.host.cancel(this.frame);
    this.frame = null;
  }

  tick(now) {
    this.frame = null;
    const a = this.anim;
    if (!a) return;
    if (a.mode === 'momentum') this.tickMomentum(a, now);
    else if (a.mode === 'spring') this.tickSpring(a, now);
    else if (a.mode === 'snap') this.tickSnap(a, now);
    if (this.anim && this.frame == null) this.frame = this.host.schedule((t) => this.tick(t));
  }

  tickMomentum(a, now) {
    const { slot, D, motion, cover } = this.ctx;
    if (a.last == null) {
      a.last = now;
      return;
    }
    const dt = Math.min(0.05, Math.max(0, (now - a.last) / 1000));
    a.last = now;
    const step = momentumStep(a.raw, a.v, dt, motion.friction);
    a.raw = step.M;
    a.v = step.v;
    const speed = Math.hypot(a.v[0], a.v[1]);
    if (!cover) {
      this.raw = a.raw;
      this.render(a.raw);
      if (speed < this.stopSpeed()) {
        this.anim = null;
        this.commit(a.raw);
      }
      return;
    }
    const el = elasticView(slot, a.raw, D, motion);
    this.raw = el.raw;
    this.render(el.view);
    if (!equals(el.raw, a.raw, 1e-12)) {
      // 越えられない（端で止まる）: 速度を捨てて今の位置で止める
      this.settle(ZERO6);
    } else if (el.over) {
      // 端を越えた: 速度をばねの初速に引き継ぐ（ゴムの傾きの分だけ鈍らせる）
      this.settle([0, 0, 0, 0, a.v[0] * motion.rubber, a.v[1] * motion.rubber]);
    } else if (speed < this.stopSpeed()) {
      this.settle(ZERO6);
    }
  }

  tickSpring(a, now) {
    if (a.t0 == null) a.t0 = now;
    const r = springMatrix(a.from, a.target, a.v0, this.ctx.motion.springPeriod, (now - a.t0) / 1000, this.tolerance());
    if (r.done) {
      this.anim = null;
      this.commit(a.target);
    } else {
      this.render(r.M);
    }
  }

  tickSnap(a, now) {
    if (a.t0 == null) a.t0 = now;
    const t = (now - a.t0) / 1000 / a.dur;
    if (t >= 1) {
      this.anim = null;
      this.commit(a.target, a.opts);
    } else {
      this.render(lerpSimilarity(a.from, a.target, easeOutCubic(t), this.imageCenter()));
    }
  }

  /** ばねで覆える位置へ戻す（cover でなければその場で確定） */
  settle(v0 = ZERO6) {
    const { slot, D, motion, cover } = this.ctx;
    const target = cover ? coverCorrection(slot, this.view, D).M : this.view;
    if (!(motion.springPeriod > 0) || equals(target, this.view, 1e-12)) {
      this.anim = null;
      this.commit(target);
      return;
    }
    this.start({ mode: 'spring', from: this.view, target, v0, t0: null });
  }

  snapTo(target, opts) {
    const dur = this.ctx.motion.snapDuration;
    if (!(dur > 0) || equals(target, this.view, 1e-12)) {
      this.anim = null;
      this.stopFrame();
      this.commit(target, opts);
      return;
    }
    this.start({ mode: 'snap', from: this.view, target, dur, t0: null, opts });
  }

  /** 動いているものを止める。慣性・ばねの最中なら、見えている位置になる生の位置を求め直す */
  interrupt() {
    const a = this.anim;
    if (!a) return;
    this.anim = null;
    this.stopFrame();
    if (a.mode === 'snap') {
      this.commit(a.target, a.opts);
      return;
    }
    const { slot, D, motion, cover } = this.ctx;
    this.raw = cover ? rawFromView(slot, this.view, D, motion) : this.view;
  }

  /** すべてを目標まで飛ばして確定する（スロットや文書の切り替え、外れるとき） */
  finish() {
    if (!this.ctx) return;
    const { slot, D, cover } = this.ctx;
    const a = this.anim;
    const pending = this.pointers.size > 0 || this.wheelTimer != null;
    this.pointers.clear();
    this.gesture = null;
    this.samples = [];
    if (this.wheelTimer != null) this.host.clearTimer(this.wheelTimer);
    this.wheelTimer = null;
    this.wheelRaw = null;
    this.anim = null;
    this.stopFrame();
    if (a && a.target) this.commit(a.target, a.opts);
    else if (a || pending) this.commit(cover ? coverCorrection(slot, this.view, D).M : this.view);
  }

  dispose() {
    this.stopFrame();
    if (this.wheelTimer != null) this.host.clearTimer(this.wheelTimer);
    this.wheelTimer = null;
    this.anim = null;
  }

  // ── ポインタ ──

  pointerDown(id, p, t) {
    if (!this.ctx || this.pointers.size >= 2 || this.pointers.has(id)) return false;
    this.interrupt();
    if (this.wheelTimer != null) {
      this.host.clearTimer(this.wheelTimer);
      this.wheelTimer = null;
      this.wheelRaw = null;
    }
    this.gesture ??= { startM: null, startPts: null, multi: false };
    this.pointers.set(id, p);
    if (this.pointers.size === 2) this.gesture.multi = true;
    this.rebase();
    this.samples = this.pointers.size === 1 ? [{ t, p }] : [];
    return true;
  }

  pointerMove(id, p, t) {
    if (!this.pointers.has(id)) return;
    this.pointers.set(id, p);
    if (this.pointers.size === 1) {
      this.samples.push({ t, p });
      while (this.samples.length > 2 && t - this.samples[0].t > 100) this.samples.shift();
    }
    this.applyGesture();
  }

  pointerUp(id, t, { cancel = false } = {}) {
    if (!this.pointers.has(id)) return;
    this.pointers.delete(id);
    if (this.pointers.size === 1) {
      this.rebase();
      this.samples = [];
      return;
    }
    if (this.pointers.size > 0) return;
    const multi = this.gesture?.multi;
    this.gesture = null;
    const v = !cancel && !multi ? velocity(this.samples, t) : null;
    this.samples = [];
    const { motion, focusWidthC } = this.ctx;
    const speed = v ? Math.hypot(v[0], v[1]) : 0;
    if (v && motion.friction > 0 && speed > 0 && speed >= motion.flingSpeed * focusWidthC) {
      this.start({ mode: 'momentum', raw: this.raw, v, last: null });
    } else {
      this.settle(ZERO6);
    }
  }

  rebase() {
    this.gesture.startM = this.raw;
    this.gesture.startPts = new Map(this.pointers);
  }

  applyGesture() {
    const { slot, D, motion, cover, rotationCover, rotationSnap } = this.ctx;
    const g = this.gesture;
    const ids = [...this.pointers.keys()];
    let delta;
    let thetaDeg = 0;
    let pivot;
    if (ids.length === 1) {
      const a0 = g.startPts.get(ids[0]);
      const a1 = this.pointers.get(ids[0]);
      delta = translate(a1[0] - a0[0], a1[1] - a0[1]);
      pivot = a1;
    } else {
      const [a0, b0] = ids.map((id) => g.startPts.get(id));
      const [a1, b1] = ids.map((id) => this.pointers.get(id));
      const v0 = [b0[0] - a0[0], b0[1] - a0[1]];
      const v1 = [b1[0] - a1[0], b1[1] - a1[1]];
      const len0 = Math.hypot(v0[0], v0[1]);
      const s = len0 > 0 ? Math.hypot(v1[0], v1[1]) / len0 : 1;
      thetaDeg = ((Math.atan2(v1[1], v1[0]) - Math.atan2(v0[1], v0[0])) * 180) / Math.PI;
      if (rotationSnap > 0) {
        const total = rotationOf(g.startM) + thetaDeg;
        const nearest = Math.round(total / 90) * 90;
        if (Math.abs(total - nearest) <= rotationSnap) thetaDeg += nearest - total;
      }
      delta = compose(translate(a1[0], a1[1]), rotate(thetaDeg), scale(s), translate(-a0[0], -a0[1]));
      pivot = a1;
    }
    let raw = this.clampScale(multiply(delta, g.startM), pivot);
    if (cover && rotationCover === 'grow' && thetaDeg !== 0) {
      // 回転で要る拡大は、ゴムを通さず即座にかける。回転を戻せば 1 に戻る
      const c = clipCenter(slot);
      const k = Math.max(1, coverScale(slot, multiply(about(c, rotate(thetaDeg)), g.startM), D));
      if (k > 1) raw = multiply(about(c, scale(k)), raw);
    }
    this.raw = raw;
    if (!cover) {
      this.render(raw);
      return;
    }
    const el = elasticView(slot, raw, D, motion);
    if (!equals(el.raw, raw, 1e-12)) {
      // 端で止まった: 基準を取り直し、指を戻した瞬間に画像が動くようにする
      this.raw = el.raw;
      this.rebase();
    }
    this.render(el.view);
  }

  clampScale(M, pivot) {
    const { slot, minScale, maxScale } = this.ctx;
    const base = scaleOf(slot.M0);
    const s = scaleOf(M) / base;
    const bound = Math.min(Math.max(s, minScale), maxScale);
    if (bound === s) return M;
    return multiply(about(pivot, scale(bound / s)), M);
  }

  // ── ホイール ──

  wheel(p, dy, rotateMode) {
    if (!this.ctx || this.pointers.size) return;
    this.interrupt();
    const { slot, D, motion, cover, rotationCover } = this.ctx;
    const base = this.wheelRaw ?? this.raw ?? this.view;
    const delta = rotateMode ? about(p, rotate(dy * 0.2)) : about(p, scale(Math.exp(-dy * 0.0015)));
    let raw = this.clampScale(multiply(delta, base), p);
    if (cover && rotateMode && rotationCover === 'grow') {
      const c = clipCenter(slot);
      const k = Math.max(1, coverScale(slot, raw, D));
      if (k > 1) raw = multiply(about(c, scale(k)), raw);
    }
    if (cover) {
      const el = elasticView(slot, raw, D, motion);
      raw = el.raw;
      this.render(el.view);
    } else {
      this.render(raw);
    }
    this.wheelRaw = raw;
    this.raw = raw;
    if (this.wheelTimer != null) this.host.clearTimer(this.wheelTimer);
    this.wheelTimer = this.host.setTimer(() => {
      this.wheelTimer = null;
      this.wheelRaw = null;
      this.settle(ZERO6);
    }, motion.wheelSettleDelay * 1000);
  }

  // ── ボタン・キー ──

  discrete(make, opts = {}) {
    if (!this.ctx || this.pointers.size) return;
    const { slot, D, cover } = this.ctx;
    const base = this.anim?.target ?? this.view;
    if (this.anim) {
      this.anim = null;
      this.stopFrame();
    }
    let target = this.clampScale(make(base), clipCenter(slot));
    if (cover) target = coverCorrection(slot, target, D).M;
    this.snapTo(target, opts);
  }

  nudge(dx, dy) {
    this.discrete((M) => multiply(translate(dx, dy), M), { mergeKey: true });
  }

  zoom(factor, opts) {
    const c = clipCenter(this.ctx.slot);
    this.discrete((M) => multiply(about(c, scale(factor)), M), opts);
  }

  rotateBy(deg, opts) {
    const c = clipCenter(this.ctx.slot);
    this.discrete((M) => multiply(about(c, rotate(deg)), M), opts);
  }

  fit() {
    const { slot, D } = this.ctx;
    this.discrete((M) => fitMatrix(slot, M, D));
  }

  reset() {
    if (!this.ctx || this.pointers.size) return;
    this.interrupt();
    this.snapTo(this.ctx.slot.M0);
  }

  undo() {
    if (!this.ctx || this.pointers.size) return;
    this.interrupt();
    const h = this.hist(this.slotName);
    const e = h.past.pop();
    if (!e) return;
    h.future.push(e);
    h.lastKeyAt = null;
    this.snapTo(e.before, { record: false });
  }

  redo() {
    if (!this.ctx || this.pointers.size) return;
    this.interrupt();
    const h = this.hist(this.slotName);
    const e = h.future.pop();
    if (!e) return;
    h.past.push(e);
    h.lastKeyAt = null;
    this.snapTo(e.after, { record: false });
  }

  // ── 小道具 ──

  imageCenter() {
    const im = this.ctx.slot.image;
    return [im.x + im.width / 2, im.y + im.height / 2];
  }

  // ばねが止まったとみなす誤差（画面で 0.5px）
  tolerance() {
    const pxPerC = this.ctx.pxPerC();
    const offset = 0.5 / pxPerC;
    const im = this.ctx.slot.image;
    return { offset, linear: offset / Math.max(im.width, im.height) };
  }

  // 慣性が止まる速さ（C 空間の単位 / 秒）
  stopSpeed() {
    const { motion, focusWidthC } = this.ctx;
    return Math.max((motion.flingSpeed * focusWidthC) / 10, 5 / this.ctx.pxPerC());
  }
}

/** 離した時刻 t から 100ms 以内の点で速度（単位 / 秒）を求める */
export function velocity(samples, t) {
  const recent = samples.filter((s) => t - s.t <= 100);
  if (recent.length < 2) return null;
  const a = recent[0];
  const b = recent[recent.length - 1];
  const dt = (b.t - a.t) / 1000;
  if (!(dt > 0)) return null;
  return [(b.p[0] - a.p[0]) / dt, (b.p[1] - a.p[1]) / dt];
}
