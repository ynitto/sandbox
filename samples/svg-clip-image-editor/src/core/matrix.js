// 2D アフィン行列。SVG の matrix(a b c d e f) と同じ 6 要素の配列で表す。
//   | a c e |
//   | b d f |
//   | 0 0 1 |

export const IDENTITY = Object.freeze([1, 0, 0, 1, 0, 0]);

/** m · n（n を先に適用し、その後に m を適用する） */
export function multiply(m, n) {
  const [a, b, c, d, e, f] = m;
  const [A, B, C, D, E, F] = n;
  return [
    a * A + c * B,
    b * A + d * B,
    a * C + c * D,
    b * C + d * D,
    a * E + c * F + e,
    b * E + d * F + f,
  ];
}

/** 左から順に掛ける: compose(A, B, C) = A · B · C */
export function compose(...ms) {
  return ms.reduce((acc, m) => multiply(acc, m), IDENTITY);
}

export function determinant(m) {
  return m[0] * m[3] - m[1] * m[2];
}

/** 逆行列。逆を持たないときは null */
export function invert(m) {
  const [a, b, c, d, e, f] = m;
  const det = a * d - b * c;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
  return [d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det];
}

export function apply(m, p) {
  return [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];
}

export function translate(x, y = 0) {
  return [1, 0, 0, 1, x, y];
}

export function scale(sx, sy = sx) {
  return [sx, 0, 0, sy, 0, 0];
}

export function rotate(deg) {
  const r = (deg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [c, s, -s, c, 0, 0];
}

/** 点 p を中心に m を掛ける: T(p) · m · T(−p) */
export function about(p, m) {
  return compose(translate(p[0], p[1]), m, translate(-p[0], -p[1]));
}

/** 全体の拡大率 sqrt(|det|) */
export function scaleOf(m) {
  return Math.sqrt(Math.abs(determinant(m)));
}

/** 回転角（度）。相似変換なら正確、スキューがあれば x 軸の向き */
export function rotationOf(m) {
  return (Math.atan2(m[1], m[0]) * 180) / Math.PI;
}

export function equals(m, n, eps = 1e-9) {
  return m.every((v, i) => Math.abs(v - n[i]) <= eps);
}

const NUM = String.raw`[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?`;
const FUNC = new RegExp(String.raw`\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)\s*,?`, 'y');
const NUMS = new RegExp(NUM, 'g');

/**
 * transform 属性の文字列を 1 つの行列にする。空文字・null は恒等。
 * 書式が壊れていれば null。
 */
export function parseTransform(text) {
  if (text == null) return IDENTITY.slice();
  const src = String(text).trim();
  if (!src) return IDENTITY.slice();
  let m = IDENTITY.slice();
  FUNC.lastIndex = 0;
  let pos = 0;
  while (pos < src.length) {
    FUNC.lastIndex = pos;
    const hit = FUNC.exec(src);
    if (!hit) return null;
    pos = FUNC.lastIndex;
    const args = (hit[2].match(NUMS) || []).map(Number);
    const rest = hit[2].replace(NUMS, '').replace(/[\s,]/g, '');
    if (rest) return null;
    const step = transformFunction(hit[1], args);
    if (!step) return null;
    m = multiply(m, step);
  }
  return m;
}

function transformFunction(name, a) {
  switch (name) {
    case 'matrix':
      return a.length === 6 ? a : null;
    case 'translate':
      return a.length === 1 || a.length === 2 ? translate(a[0], a[1] ?? 0) : null;
    case 'scale':
      return a.length === 1 || a.length === 2 ? scale(a[0], a[1] ?? a[0]) : null;
    case 'rotate':
      if (a.length === 1) return rotate(a[0]);
      if (a.length === 3) return about([a[1], a[2]], rotate(a[0]));
      return null;
    case 'skewX':
      return a.length === 1 ? [1, 0, Math.tan((a[0] * Math.PI) / 180), 1, 0, 0] : null;
    case 'skewY':
      return a.length === 1 ? [1, Math.tan((a[0] * Math.PI) / 180), 0, 1, 0, 0] : null;
    default:
      return null;
  }
}

/** 数を小数 digits 桁に丸め、末尾の 0 を落とした文字列にする */
export function formatNumber(n, digits) {
  const v = Number(n.toFixed(digits));
  return Object.is(v, -0) ? '0' : String(v);
}

/** matrix(a b c d e f)。a〜d は 6 桁、e f は precision 桁 */
export function formatMatrix(m, precision = 3) {
  const parts = m.map((v, i) => formatNumber(v, i < 4 ? 6 : precision));
  return `matrix(${parts.join(' ')})`;
}

/**
 * スキューがなければ translate / rotate / scale に分ける。スキューがあれば null。
 * m = T(e, f) · R(θ) · S(sx, sy)
 */
export function decompose(m) {
  const [a, b, c, d, e, f] = m;
  const sx = Math.hypot(a, b);
  if (sx < 1e-12) return null;
  const det = a * d - b * c;
  const sy = det / sx;
  // x 軸と y 軸が直交していなければスキューがある
  if (Math.abs(a * c + b * d) > 1e-9 * Math.max(1, sx * Math.abs(sy))) return null;
  return { translate: [e, f], rotate: (Math.atan2(b, a) * 180) / Math.PI, scale: [sx, sy] };
}

export function formatDecomposed(m, precision = 3) {
  const parts = decompose(m);
  if (!parts) return formatMatrix(m, precision);
  const out = [];
  const [tx, ty] = parts.translate;
  if (Math.abs(tx) > 1e-12 || Math.abs(ty) > 1e-12) {
    out.push(`translate(${formatNumber(tx, precision)} ${formatNumber(ty, precision)})`);
  }
  if (Math.abs(parts.rotate) > 1e-9) out.push(`rotate(${formatNumber(parts.rotate, 6)})`);
  const [sx, sy] = parts.scale;
  if (Math.abs(sx - 1) > 1e-12 || Math.abs(sy - 1) > 1e-12) {
    out.push(Math.abs(sx - sy) < 1e-12 ? `scale(${formatNumber(sx, 6)})` : `scale(${formatNumber(sx, 6)} ${formatNumber(sy, 6)})`);
  }
  return out.join(' ');
}
