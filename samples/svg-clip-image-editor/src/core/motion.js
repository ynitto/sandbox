// ゴム・慣性・ばね。時刻や経過時間は引数で受け取る純粋関数にして、偽の時計で試せるようにする。

import { coverScale, coverTranslation } from './cover.js';
import { clipCenter } from './geometry.js';
import { about, multiply, scale, scaleOf, translate } from './matrix.js';

export const DEFAULT_MOTION = Object.freeze({
  rubber: 0.55, // ゴムの抵抗の係数
  rubberLimit: 0.25, // 平行移動で越えられる量の上限（枠の短辺に対する比）
  scaleRubberLimit: 0.5, // 縮めすぎで越えられる量の上限（倍率 − 1）
  friction: 0.325, // 慣性が減速する時定数（秒）
  flingSpeed: 0.3, // 慣性に入る速さ（注目領域の幅 / 秒）
  springPeriod: 0.4, // ばねで戻る速さ（秒）
  snapDuration: 0.2, // ボタン・キー・切り替えで寄せる時間（秒）
  wheelSettleDelay: 0.15, // ホイールが止まってから戻し始めるまで（秒）
});

const TIME_KEYS = ['friction', 'springPeriod', 'snapDuration'];

/**
 * 指定を既定に重ねる。負の値・数でない値は既定に戻し、warnings に積む。
 * reducedMotion なら時間に関わる値を 0 にする（ゴムの抵抗は残す）。
 */
export function resolveMotion(opts = {}, { reducedMotion = false } = {}) {
  const out = { ...DEFAULT_MOTION };
  const warnings = [];
  for (const [key, value] of Object.entries(opts ?? {})) {
    if (!(key in DEFAULT_MOTION)) {
      warnings.push(`motion.${key} は知らないキー`);
      continue;
    }
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      warnings.push(`motion.${key} は 0 以上の数にする（既定 ${DEFAULT_MOTION[key]} を使う）`);
      continue;
    }
    out[key] = value;
  }
  if (reducedMotion) for (const k of TIME_KEYS) out[k] = 0;
  return { motion: out, warnings };
}

/** ゴムの関数: 越えた量 d → 見せる越え量。c か L が 0 なら 0（越えない） */
export function rubber(d, L, c) {
  if (!(c > 0) || !(L > 0) || !(d > 0)) return 0;
  return L * (1 - 1 / ((d * c) / L + 1));
}

/** rubber の逆関数: 見せる越え量 r → 越えた量 d */
export function rubberInverse(r, L, c) {
  if (!(c > 0) || !(L > 0) || !(r > 0)) return 0;
  const rr = Math.min(r, L * (1 - 1e-9));
  return (L / c) * (1 / (1 - rr / L) - 1);
}

const isScaleHard = (m) => !(m.rubber > 0) || !(m.scaleRubberLimit > 0);
const isTranslateHard = (m) => !(m.rubber > 0) || !(m.rubberLimit > 0);

// 平行移動のゴムの上限 L（I 空間）: 枠の短辺を I 空間に写した長さ × rubberLimit
function translateLimit(slot, M, motion) {
  const b = slot.clip.bboxC;
  return (Math.min(b.width, b.height) / scaleOf(M)) * motion.rubberLimit;
}

/**
 * 生の行列 → 見せる行列。
 * 越えられない成分（rubber か上限が 0）は全部補正し、その結果を raw として返す。
 * 呼ぶ側は raw が入力と違えばジェスチャの基準を取り直す（指を戻した瞬間に動くように）。
 * @returns {{ view: number[], raw: number[], over: boolean }}
 */
export function elasticView(slot, Mraw, D, motion) {
  const c = clipCenter(slot);
  let raw = Mraw;
  if (isScaleHard(motion)) {
    const k = coverScale(slot, raw, D);
    if (k > 1) raw = multiply(about(c, scale(k)), raw);
  }
  if (isTranslateHard(motion)) {
    const u = coverTranslation(slot, raw, D);
    if (u[0] || u[1]) raw = multiply(raw, translate(u[0], u[1]));
  }
  let view = raw;
  let over = false;
  if (!isScaleHard(motion)) {
    const k = coverScale(slot, view, D);
    if (k > 1) {
      over = true;
      const s = Math.log(k);
      const shown = rubber(s, Math.log(1 + motion.scaleRubberLimit), motion.rubber);
      view = multiply(about(c, scale(Math.exp(s - shown))), view);
    }
  }
  if (!isTranslateHard(motion)) {
    const u = coverTranslation(slot, view, D);
    const n = Math.hypot(u[0], u[1]);
    if (n > 0) {
      over = true;
      const shown = rubber(n, translateLimit(slot, view, motion), motion.rubber);
      const f = 1 - shown / n;
      view = multiply(view, translate(u[0] * f, u[1] * f));
    }
  }
  return { view, raw, over };
}

/**
 * 見せている行列から、その見え方になる生の行列を求める（アニメーション中に掴んだとき、跳ねないように）。
 * elasticView の逆を、平行移動 → 拡大の順にたどる。
 */
export function rawFromView(slot, view, D, motion) {
  const c = clipCenter(slot);
  let raw = view;
  if (!isTranslateHard(motion)) {
    const u = coverTranslation(slot, raw, D);
    const r = Math.hypot(u[0], u[1]);
    if (r > 0) {
      const d = rubberInverse(r, translateLimit(slot, raw, motion), motion.rubber);
      raw = multiply(raw, translate((u[0] * (r - d)) / r, (u[1] * (r - d)) / r));
    }
  }
  if (!isScaleHard(motion)) {
    const k = coverScale(slot, raw, D);
    if (k > 1) {
      const r = Math.log(k);
      const s = rubberInverse(r, Math.log(1 + motion.scaleRubberLimit), motion.rubber);
      raw = multiply(about(c, scale(Math.exp(r - s))), raw);
    }
  }
  return raw;
}

/** 慣性の 1 ステップ: C 空間の速度 v（単位 / 秒）で dt 秒進め、摩擦で減速する */
export function momentumStep(Mraw, v, dt, friction) {
  const next = multiply(translate(v[0] * dt, v[1] * dt), Mraw);
  const decay = friction > 0 ? Math.exp(-dt / friction) : 0;
  return { M: next, v: [v[0] * decay, v[1] * decay] };
}

/**
 * 臨界減衰のばね。x0 は目標からのずれ、v0 は初速、t 秒後のずれと速度を返す。
 * period が 0 なら即座に目標（ずれ 0）。
 */
export function springAt(x0, v0, period, t) {
  if (!(period > 0)) return { x: 0, v: 0 };
  const w = (2 * Math.PI) / period;
  const B = v0 + w * x0;
  const e = Math.exp(-w * t);
  return { x: (x0 + B * t) * e, v: (v0 - w * B * t) * e };
}

/**
 * 行列の 6 成分それぞれにばねをかける（覆う補正は回転を変えないので、成分ごとの補間で相似変換のまま）。
 * v0 は 6 成分の初速。done は目標に十分近いか。
 */
export function springMatrix(from, target, v0, period, t, eps) {
  let done = true;
  const M = from.map((f, i) => {
    const { x, v } = springAt(f - target[i], v0[i], period, t);
    // a〜d は大きさの比なので、画像の大きさを掛けた量で比べる（eps.linear）
    const tol = i < 4 ? eps.linear : eps.offset;
    if (Math.abs(x) > tol || Math.abs(v) > tol * 10) done = false;
    return target[i] + x;
  });
  return { M: done ? target.slice() : M, done };
}

/** 0→1 のなめらかな補間（ボタン・キーで寄せるとき） */
export function easeOutCubic(t) {
  const u = Math.min(1, Math.max(0, t));
  return 1 - (1 - u) ** 3;
}

export function lerpMatrix(a, b, t) {
  return a.map((v, i) => v + (b[i] - v) * t);
}

/**
 * 相似変換どうしの補間。回転は近い向きへ、拡大率は比で、位置は画像の中心 pI（I 空間）の行き先で補間する。
 * スキューがあれば成分ごとの補間に戻す。
 */
export function lerpSimilarity(a, b, t, pI) {
  const da = similarityParts(a);
  const db = similarityParts(b);
  if (!da || !db) return lerpMatrix(a, b, t);
  let dθ = db.theta - da.theta;
  while (dθ > Math.PI) dθ -= 2 * Math.PI;
  while (dθ < -Math.PI) dθ += 2 * Math.PI;
  const theta = da.theta + dθ * t;
  const s = da.s * (db.s / da.s) ** t;
  const flip = da.flip;
  const cos = Math.cos(theta) * s;
  const sin = Math.sin(theta) * s;
  const lin = [cos, sin, -sin * flip, cos * flip];
  const pa = [a[0] * pI[0] + a[2] * pI[1] + a[4], a[1] * pI[0] + a[3] * pI[1] + a[5]];
  const pb = [b[0] * pI[0] + b[2] * pI[1] + b[4], b[1] * pI[0] + b[3] * pI[1] + b[5]];
  const p = [pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t];
  return [lin[0], lin[1], lin[2], lin[3], p[0] - (lin[0] * pI[0] + lin[2] * pI[1]), p[1] - (lin[1] * pI[0] + lin[3] * pI[1])];
}

// 相似変換なら { theta, s, flip }（flip は鏡映なら −1）。違えば null
function similarityParts(m) {
  const [a, b, c, d] = m;
  const s = Math.hypot(a, b);
  if (s < 1e-12) return null;
  const det = a * d - b * c;
  const flip = det < 0 ? -1 : 1;
  if (Math.abs(a - d * flip) > 1e-9 * s || Math.abs(b + c * flip) > 1e-9 * s) return null;
  return { theta: Math.atan2(b, a), s, flip };
}
