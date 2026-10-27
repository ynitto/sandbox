// SVG 文字列を解析し、スロットごとの座標の変換・画像・枠の形・凸包を求める。
// 例外は投げず、{ ok: true, model } か { ok: false, code, message, issues? } を返す。

import { parseCssRules } from './css.js';
import { allElements, clipRefId, elementChildren, hrefOf, parseViewBox, pathOf } from './dom.js';
import { bboxOf, clipHull, polygonArea } from './geometry.js';
import { hasErrors, lintDocument } from './lint.js';
import { compose, invert, parseTransform } from './matrix.js';

const GEOMETRY_ATTRS = ['x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r', 'points', 'd', 'clip-rule'];

/**
 * @param {string} svg
 * @param {{ DOMParser?: typeof DOMParser, parseCss?: Function }} [env] Node では jsdom の DOMParser を渡す
 */
export function parseClipDocument(svg, env = {}) {
  const Parser = env.DOMParser ?? globalThis.DOMParser;
  if (!Parser) return { ok: false, code: 'parse_error', message: 'DOMParser がない環境では読めない' };

  let doc;
  try {
    doc = new Parser().parseFromString(String(svg ?? ''), 'image/svg+xml');
  } catch (err) {
    return { ok: false, code: 'parse_error', message: `XML として読めない: ${err.message}` };
  }
  const root = doc.documentElement;
  if (!root || doc.getElementsByTagName('parsererror').length || root.localName !== 'svg') {
    return { ok: false, code: 'parse_error', message: 'XML として読めないか、ルートが <svg> ではない' };
  }

  const issues = lintDocument(doc, env.parseCss ?? parseCssRules);
  if (hasErrors(issues)) {
    return {
      ok: false,
      code: 'contract_violation',
      message: `SVG が契約を満たしていない（${issues.filter((i) => i.severity === 'error').length} 件）`,
      issues,
    };
  }

  const byId = new Map(allElements(root).filter((el) => el.hasAttribute('id')).map((el) => [el.getAttribute('id'), el]));
  const slots = [];
  for (const slotEl of allElements(root).filter((el) => el.hasAttribute('data-clip-slot'))) {
    const name = slotEl.getAttribute('data-clip-slot');

    // K: ルート直下からスロットの <g> 自身までの transform の積
    const chain = [];
    for (let el = slotEl; el && el !== root; el = el.parentNode) chain.unshift(el);
    const K = compose(...chain.map((el) => parseTransform(el.getAttribute('transform'))));

    const imageEl = elementChildren(slotEl)[0];
    const M0 = parseTransform(imageEl.getAttribute('transform'));
    const href = hrefOf(imageEl);
    const image = {
      href: href.value,
      hrefAttr: href.attr,
      x: Number(imageEl.getAttribute('x') ?? 0),
      y: Number(imageEl.getAttribute('y') ?? 0),
      width: Number(imageEl.getAttribute('width')),
      height: Number(imageEl.getAttribute('height')),
      preserveAspectRatio: imageEl.getAttribute('preserveAspectRatio') || 'xMidYMid meet',
    };

    const clipId = clipRefId(slotEl.getAttribute('clip-path'));
    const clipEl = byId.get(clipId);
    const Q = parseTransform(clipEl.getAttribute('transform'));
    const shapes = elementChildren(clipEl).map((el) => ({
      tag: el.localName,
      attrs: Object.fromEntries(GEOMETRY_ATTRS.filter((a) => el.hasAttribute(a)).map((a) => [a, el.getAttribute(a)])),
      transform: parseTransform(el.getAttribute('transform')),
    }));

    if (!invert(K) || !invert(M0) || !invert(Q)) {
      return { ok: false, code: 'singular_transform', message: `スロット "${name}" の座標の変換が逆行列を持たない` };
    }

    let hullC;
    try {
      hullC = clipHull(shapes, Q);
    } catch (err) {
      return { ok: false, code: 'contract_violation', message: `スロット "${name}" の枠を読めない: ${err.message}` };
    }
    if (hullC.length < 3 || polygonArea(hullC) < 1e-9) {
      return { ok: false, code: 'empty_clip', message: `スロット "${name}" の枠の面積が 0` };
    }

    slots.push({
      name,
      path: pathOf(slotEl),
      K,
      M0,
      image,
      clip: { id: clipId, Q, shapes, bboxC: bboxOf(hullC), hullC },
    });
  }

  if (!slots.length) return { ok: false, code: 'no_slot', message: 'スロット（data-clip-slot）が 1 つもない' };

  return {
    ok: true,
    model: {
      source: String(svg),
      doc,
      rootViewBox: parseViewBox(root.getAttribute('viewBox')),
      slots,
      warnings: issues,
    },
  };
}
