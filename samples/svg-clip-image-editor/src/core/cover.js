// 覆い制約: 枠の内側に画像のない所を作らない。
// 画像の描画矩形 D は I 空間で軸に沿った矩形（C 空間では凸な平行四辺形）なので、
// 「枠を覆う」⇔「枠の凸包の全頂点が D に入る」⇔「凸包を I 空間に写した外接矩形が D に入る」。

import { bboxOf, clipCenter } from './geometry.js';
import { about, apply, invert, multiply, scale, translate } from './matrix.js';

/** 凸包を I 空間に写した外接矩形 */
export function hullInImage(slot, M) {
  const inv = invert(M);
  return bboxOf(slot.clip.hullC.map((p) => apply(inv, p)));
}

/** 今の回転のまま D に収めるのに要る拡大率（1 未満もあり得る） */
export function coverScale(slot, M, D) {
  const q = hullInImage(slot, M);
  return Math.max(q.width / D.width, q.height / D.height);
}

// lo..hi のうち 0 に一番近い値。lo > hi（幅が足りず収まらない）なら真ん中
function nearestToZero(lo, hi) {
  if (lo > hi) return (lo + hi) / 2;
  return Math.min(Math.max(0, lo), hi);
}

/** I 空間での平行移動 u（M ← M · T(u)）。今の位置から一番近い、覆える位置 */
export function coverTranslation(slot, M, D) {
  const q = hullInImage(slot, M);
  return [
    nearestToZero(q.x + q.width - (D.x + D.width), q.x - D.x),
    nearestToZero(q.y + q.height - (D.y + D.height), q.y - D.y),
  ];
}

/**
 * 覆うための最小の補正。拡大（枠の中心で k ≥ 1 倍）→ 平行移動（I 空間で u）の順。
 * @returns {{ k: number, c: number[], u: number[], M: number[] }} M は補正後の行列
 */
export function coverCorrection(slot, M, D) {
  const c = clipCenter(slot);
  const k = Math.max(1, coverScale(slot, M, D));
  const M1 = k > 1 ? multiply(about(c, scale(k)), M) : M;
  const u = coverTranslation(slot, M1, D);
  return { k, c, u, M: multiply(M1, translate(u[0], u[1])) };
}

export function isCovered(slot, M, D, rel = 1e-6) {
  const { k, u } = coverCorrection(slot, M, D);
  return k <= 1 + rel && Math.hypot(u[0], u[1]) <= rel * Math.max(D.width, D.height);
}

/** 「枠に合わせる」: 今の回転のまま、枠を丁度覆う大きさ（縮めることもある）にして中央に置く */
export function fitMatrix(slot, M, D) {
  const c = clipCenter(slot);
  const k = coverScale(slot, M, D);
  const M1 = multiply(about(c, scale(k)), M);
  const q = hullInImage(slot, M1);
  const u = [q.x + q.width / 2 - (D.x + D.width / 2), q.y + q.height / 2 - (D.y + D.height / 2)];
  return multiply(M1, translate(u[0], u[1]));
}
