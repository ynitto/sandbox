// 枠の凸包・外接矩形、画像の描画矩形、注目領域。

import { apply, compose } from './matrix.js';
import { parsePathData, shapeToPath } from './path.js';

/** 点の外接矩形 { x, y, width, height } */
export function bboxOf(points) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [x, y] of points) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  if (x0 === Infinity) return { x: 0, y: 0, width: 0, height: 0 };
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** 凸包（Andrew の monotone chain）。反時計回り（y 下向きの画面では時計回りに見える）の頂点列 */
export function convexHull(points) {
  const pts = points
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
    .slice()
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

export function polygonArea(poly) {
  let s = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i];
    const [x1, y1] = poly[(i + 1) % poly.length];
    s += x0 * y1 - x1 * y0;
  }
  return Math.abs(s) / 2;
}

// 点 p と線分 a-b の距離
function distToSegment(p, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

// 3 次ベジェを、制御点と弦の距離が eps 以下になるまで二分割し、分割後の制御点をすべて集める。
// 曲線は制御点の凸包の中にあるので、集めた点の凸包は曲線を必ず含む。
function collectCubic(p, eps, out, depth = 0) {
  const [p0, c1, c2, p3] = p;
  const flat = Math.max(distToSegment(c1, p0, p3), distToSegment(c2, p0, p3));
  if (flat <= eps || depth >= 16) {
    out.push(p0, c1, c2, p3);
    return;
  }
  const a = mid(p0, c1);
  const b = mid(c1, c2);
  const c = mid(c2, p3);
  const ab = mid(a, b);
  const bc = mid(b, c);
  const m = mid(ab, bc);
  collectCubic([p0, a, ab, m], eps, out, depth + 1);
  collectCubic([m, bc, c, p3], eps, out, depth + 1);
}

/**
 * 枠の形（ClipShape の列）を C 空間の区分にする。
 * shape: { tag, attrs, transform: Matrix }、Q: <clipPath> の transform
 */
export function clipSegments(shapes, Q) {
  const segs = [];
  for (const shape of shapes) {
    const d = shapeToPath(shape.tag, shape.attrs);
    if (!d) continue;
    const m = compose(Q, shape.transform);
    for (const seg of parsePathData(d)) {
      segs.push({ type: seg.type, p: seg.p.map((pt) => apply(m, pt)) });
    }
  }
  return segs;
}

/**
 * 枠の凸包（C 空間）。eps を省くと外接矩形の対角線の 0.1%。
 * 返す凸包は真の凸包を含み、はみ出しは eps 以下。
 */
export function clipHull(shapes, Q, eps) {
  const segs = clipSegments(shapes, Q);
  if (eps == null) {
    const box = bboxOf(segs.flatMap((s) => s.p));
    eps = Math.hypot(box.width, box.height) * 0.001 || 1e-6;
  }
  const pts = [];
  for (const seg of segs) {
    if (seg.type === 'L') pts.push(seg.p[0], seg.p[1]);
    else collectCubic(seg.p, eps, pts);
  }
  return convexHull(pts);
}

/**
 * 画像が実際に描かれる矩形（I 空間）。
 * none / slice は x y width height の矩形そのもの。meet は画像の元の大きさ natural が分かれば内側の矩形。
 */
export function drawnImageRect(image, natural) {
  const rect = { x: image.x, y: image.y, width: image.width, height: image.height };
  const par = (image.preserveAspectRatio || 'xMidYMid meet').trim().split(/\s+/);
  const align = par[0];
  const mode = par[1] || 'meet';
  if (align === 'none' || mode === 'slice' || !natural || !(natural.width > 0 && natural.height > 0)) return rect;
  const s = Math.min(rect.width / natural.width, rect.height / natural.height);
  const w = natural.width * s;
  const h = natural.height * s;
  const ax = /xMax/.test(align) ? 1 : /xMin/.test(align) ? 0 : 0.5;
  const ay = /YMax/.test(align) ? 1 : /YMin/.test(align) ? 0 : 0.5;
  return { x: rect.x + (rect.width - w) * ax, y: rect.y + (rect.height - h) * ay, width: w, height: h };
}

/** 注目領域: 凸包をルート座標へ写した外接矩形に、各辺 padding（幅・高さに対する比）の余白を足す */
export function focusRect(slot, padding = 0.25) {
  const box = bboxOf(slot.clip.hullC.map((p) => apply(slot.K, p)));
  const px = box.width * padding;
  const py = box.height * padding;
  return { x: box.x - px, y: box.y - py, width: box.width + 2 * px, height: box.height + 2 * py };
}

/** 枠の中心（C 空間）: 凸包の外接矩形の中心 */
export function clipCenter(slot) {
  const b = slot.clip.bboxC;
  return [b.x + b.width / 2, b.y + b.height / 2];
}
