// パスと基本図形を「直線と 3 次ベジェ」の列にする。
// 区分: { type: 'L', p: [p0, p1] } / { type: 'C', p: [p0, c1, c2, p1] }

const NUMBER = /[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y;

/** パスの d を区分の列にする。書式が壊れていれば Error を投げる */
export function parsePathData(d) {
  const src = String(d ?? '');
  const segs = [];
  let i = 0;
  let cmd = null;
  let cur = [0, 0];
  let start = [0, 0];
  let lastCtrl = null; // 直前の C/S の第 2 制御点（S の反射用）
  let lastQ = null; // 直前の Q/T の制御点（T の反射用）

  const skip = () => {
    while (i < src.length && /[\s,]/.test(src[i])) i++;
  };
  const num = () => {
    skip();
    NUMBER.lastIndex = i;
    const m = NUMBER.exec(src);
    if (!m) throw new Error(`パスの数が足りない: ${d}`);
    i = NUMBER.lastIndex;
    return Number(m[0]);
  };
  // 円弧のフラグは区切りなしで詰めて書けるので（"a1 1 0 01 1 1"）、1 文字ずつ読む
  const flag = () => {
    skip();
    const c = src[i];
    if (c !== '0' && c !== '1') throw new Error(`円弧のフラグが 0/1 でない: ${d}`);
    i++;
    return Number(c);
  };

  for (;;) {
    skip();
    if (i >= src.length) break;
    if (/[A-Za-z]/.test(src[i])) {
      cmd = src[i++];
    } else if (cmd == null) {
      throw new Error(`パスがコマンドで始まらない: ${d}`);
    } else if (cmd === 'M') {
      cmd = 'L';
    } else if (cmd === 'm') {
      cmd = 'l';
    } else if (cmd === 'Z' || cmd === 'z') {
      throw new Error(`Z の後に数がある: ${d}`);
    }
    const upper = cmd.toUpperCase();
    const rel = cmd !== upper;
    const ox = rel ? cur[0] : 0;
    const oy = rel ? cur[1] : 0;
    let nextCtrl = null;
    let nextQ = null;

    switch (upper) {
      case 'M': {
        cur = [ox + num(), oy + num()];
        start = cur;
        break;
      }
      case 'L': {
        const p = [ox + num(), oy + num()];
        segs.push({ type: 'L', p: [cur, p] });
        cur = p;
        break;
      }
      case 'H': {
        const p = [rel ? cur[0] + num() : num(), cur[1]];
        segs.push({ type: 'L', p: [cur, p] });
        cur = p;
        break;
      }
      case 'V': {
        const p = [cur[0], rel ? cur[1] + num() : num()];
        segs.push({ type: 'L', p: [cur, p] });
        cur = p;
        break;
      }
      case 'C': {
        const c1 = [ox + num(), oy + num()];
        const c2 = [ox + num(), oy + num()];
        const p = [ox + num(), oy + num()];
        segs.push({ type: 'C', p: [cur, c1, c2, p] });
        nextCtrl = c2;
        cur = p;
        break;
      }
      case 'S': {
        const c1 = lastCtrl ? [2 * cur[0] - lastCtrl[0], 2 * cur[1] - lastCtrl[1]] : cur;
        const c2 = [ox + num(), oy + num()];
        const p = [ox + num(), oy + num()];
        segs.push({ type: 'C', p: [cur, c1, c2, p] });
        nextCtrl = c2;
        cur = p;
        break;
      }
      case 'Q': {
        const q = [ox + num(), oy + num()];
        const p = [ox + num(), oy + num()];
        segs.push(quadToCubic(cur, q, p));
        nextQ = q;
        cur = p;
        break;
      }
      case 'T': {
        const q = lastQ ? [2 * cur[0] - lastQ[0], 2 * cur[1] - lastQ[1]] : cur;
        const p = [ox + num(), oy + num()];
        segs.push(quadToCubic(cur, q, p));
        nextQ = q;
        cur = p;
        break;
      }
      case 'A': {
        const rx = num();
        const ry = num();
        const phi = num();
        const fa = flag();
        const fs = flag();
        const p = [ox + num(), oy + num()];
        segs.push(...arcToCubics(cur, rx, ry, phi, fa, fs, p));
        cur = p;
        break;
      }
      case 'Z': {
        if (cur[0] !== start[0] || cur[1] !== start[1]) segs.push({ type: 'L', p: [cur, start] });
        cur = start;
        break;
      }
      default:
        throw new Error(`知らないパスのコマンド: ${cmd}`);
    }
    lastCtrl = nextCtrl;
    lastQ = nextQ;
  }
  return segs;
}

function quadToCubic(p0, q, p1) {
  return {
    type: 'C',
    p: [
      p0,
      [p0[0] + (2 / 3) * (q[0] - p0[0]), p0[1] + (2 / 3) * (q[1] - p0[1])],
      [p1[0] + (2 / 3) * (q[0] - p1[0]), p1[1] + (2 / 3) * (q[1] - p1[1])],
      p1,
    ],
  };
}

/** SVG の円弧（端点パラメータ化）を 90° 以下ずつの 3 次ベジェにする */
export function arcToCubics(p0, rx, ry, phiDeg, fa, fs, p1) {
  const [x1, y1] = p0;
  const [x2, y2] = p1;
  if (x1 === x2 && y1 === y2) return [];
  rx = Math.abs(rx);
  ry = Math.abs(ry);
  if (!rx || !ry) return [{ type: 'L', p: [p0, p1] }];
  const phi = (phiDeg * Math.PI) / 180;
  const cos = Math.cos(phi);
  const sin = Math.sin(phi);
  const dx = (x1 - x2) / 2;
  const dy = (y1 - y2) / 2;
  const x1p = cos * dx + sin * dy;
  const y1p = -sin * dx + cos * dy;
  const lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lam > 1) {
    const s = Math.sqrt(lam);
    rx *= s;
    ry *= s;
  }
  const rx2 = rx * rx;
  const ry2 = ry * ry;
  const num = Math.max(0, rx2 * ry2 - rx2 * y1p * y1p - ry2 * x1p * x1p);
  const den = rx2 * y1p * y1p + ry2 * x1p * x1p;
  let coef = den ? Math.sqrt(num / den) : 0;
  if (fa === fs) coef = -coef;
  const cxp = (coef * rx * y1p) / ry;
  const cyp = (-coef * ry * x1p) / rx;
  const cx = cos * cxp - sin * cyp + (x1 + x2) / 2;
  const cy = sin * cxp + cos * cyp + (y1 + y2) / 2;
  const angle = (ux, uy, vx, vy) => Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
  const t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!fs && dt > 0) dt -= 2 * Math.PI;
  else if (fs && dt < 0) dt += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(dt) / (Math.PI / 2) - 1e-9));
  const step = dt / n;
  const k = (4 / 3) * Math.tan(step / 4);
  const point = (a) => [
    cx + rx * Math.cos(a) * cos - ry * Math.sin(a) * sin,
    cy + rx * Math.cos(a) * sin + ry * Math.sin(a) * cos,
  ];
  const deriv = (a) => [
    -rx * Math.sin(a) * cos - ry * Math.cos(a) * sin,
    -rx * Math.sin(a) * sin + ry * Math.cos(a) * cos,
  ];
  const segs = [];
  for (let j = 0; j < n; j++) {
    const a0 = t1 + j * step;
    const a1 = a0 + step;
    const q0 = j === 0 ? p0 : point(a0);
    const q3 = j === n - 1 ? p1 : point(a1);
    const d0 = deriv(a0);
    const d1 = deriv(a1);
    segs.push({
      type: 'C',
      p: [q0, [q0[0] + k * d0[0], q0[1] + k * d0[1]], [q3[0] - k * d1[0], q3[1] - k * d1[1]], q3],
    });
  }
  return segs;
}

const num = (v, dflt = 0) => {
  if (v == null || v === '') return dflt;
  const n = Number(v);
  return Number.isFinite(n) ? n : NaN;
};

/**
 * 基本図形（rect / circle / ellipse / polygon / polyline / path）をパスの d にする。
 * attrs は属性名 → 文字列。描けない（幅 0 など）ときは空文字。
 */
export function shapeToPath(tag, attrs) {
  switch (tag) {
    case 'path':
      return attrs.d || '';
    case 'rect': {
      const x = num(attrs.x);
      const y = num(attrs.y);
      const w = num(attrs.width);
      const h = num(attrs.height);
      if (!(w > 0 && h > 0)) return '';
      let rx = attrs.rx != null ? num(attrs.rx) : null;
      let ry = attrs.ry != null ? num(attrs.ry) : null;
      if (rx == null && ry != null) rx = ry;
      if (ry == null && rx != null) ry = rx;
      rx = Math.min(Math.max(rx || 0, 0), w / 2);
      ry = Math.min(Math.max(ry || 0, 0), h / 2);
      if (!rx || !ry) return `M${x} ${y}H${x + w}V${y + h}H${x}Z`;
      return (
        `M${x + rx} ${y}H${x + w - rx}A${rx} ${ry} 0 0 1 ${x + w} ${y + ry}` +
        `V${y + h - ry}A${rx} ${ry} 0 0 1 ${x + w - rx} ${y + h}` +
        `H${x + rx}A${rx} ${ry} 0 0 1 ${x} ${y + h - ry}` +
        `V${y + ry}A${rx} ${ry} 0 0 1 ${x + rx} ${y}Z`
      );
    }
    case 'circle':
    case 'ellipse': {
      const cx = num(attrs.cx);
      const cy = num(attrs.cy);
      const rx = tag === 'circle' ? num(attrs.r) : num(attrs.rx);
      const ry = tag === 'circle' ? num(attrs.r) : num(attrs.ry);
      if (!(rx > 0 && ry > 0)) return '';
      return `M${cx - rx} ${cy}A${rx} ${ry} 0 1 0 ${cx + rx} ${cy}A${rx} ${ry} 0 1 0 ${cx - rx} ${cy}Z`;
    }
    case 'polygon':
    case 'polyline': {
      const pts = (attrs.points || '').trim().split(/[\s,]+/).filter(Boolean).map(Number);
      if (pts.length < 4 || pts.some((v) => !Number.isFinite(v))) return '';
      let d = `M${pts[0]} ${pts[1]}`;
      for (let i = 2; i + 1 < pts.length; i += 2) d += `L${pts[i]} ${pts[i + 1]}`;
      // クリップでは polyline も閉じた図形として塗られる
      return d + 'Z';
    }
    default:
      return '';
  }
}
