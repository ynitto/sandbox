// SVG の契約の検査（設計書 §1.1 の規則表）。
// ブラウザと Node で結果が分かれないよう、文書と CSS の読み手を受け取る形にする。

import { declarationNames, parseCssRules } from './css.js';
import {
  allElements,
  clipRefId,
  elementChildren,
  hasNonWhitespaceText,
  hrefOf,
  isPlainNumber,
  locationOf,
  parseViewBox,
  pathOf,
} from './dom.js';
import { invert, parseTransform } from './matrix.js';

const SLOT_NAME = /^[a-z0-9][a-z0-9-]*$/;
const CLIP_SHAPES = new Set(['rect', 'circle', 'ellipse', 'polygon', 'polyline', 'path']);
const FORBIDDEN_ANCESTORS = new Set(['svg', 'symbol', 'use', 'switch', 'a', 'foreignObject']);
// D4: スロット・画像・祖先に CSS で当ててはいけないプロパティ
const FORBIDDEN_CSS = new Set(['transform', 'translate', 'rotate', 'scale', 'transform-origin', 'transform-box', 'clip-path', 'mask']);
const SAFE_HREF = /^(?:data:image\/|https?:|blob:)/i;
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/** S5: 使ってよい href か */
export function isAllowedHref(href) {
  if (!href) return false;
  const v = href.trim();
  if (SAFE_HREF.test(v)) return true;
  return !HAS_SCHEME.test(v); // 相対 URL
}

/**
 * @param {Document} doc
 * @param {(text: string) => {selector: string, properties: string[]}[]} [parseCss]
 * @returns {{ rule: string, severity: 'error' | 'warning', slot?: string, path: number[], location: string, message: string }[]}
 */
export function lintDocument(doc, parseCss = parseCssRules) {
  const issues = [];
  const root = doc.documentElement;
  const add = (rule, severity, el, message, slot) =>
    issues.push({ rule, severity, slot, path: pathOf(el), location: locationOf(el), message });

  if (!root || root.localName !== 'svg') {
    issues.push({ rule: 'D1', severity: 'error', path: [], location: '/', message: 'ルートが <svg> ではない' });
    return issues;
  }

  const elements = allElements(root);

  // D1
  if (!parseViewBox(root.getAttribute('viewBox'))) add('D1', 'error', root, 'ルートの <svg> に正しい viewBox がない');

  // D2: id の一意性と id の表
  const byId = new Map();
  for (const el of elements) {
    const id = el.getAttribute('id');
    if (id == null) continue;
    if (byId.has(id)) add('D2', 'error', el, `id "${id}" が重複している`);
    else byId.set(id, el);
  }

  const slotEls = elements.filter((el) => el.hasAttribute('data-clip-slot'));
  const names = new Set();
  const guarded = []; // D4 で CSS を確かめる要素: { el, slot }
  const slotSubtrees = new Set();

  for (const slotEl of slotEls) {
    const name = slotEl.getAttribute('data-clip-slot');
    const slot = name;

    // S1
    if (slotEl.localName !== 'g') add('S1', 'error', slotEl, `スロット "${name}" が <g> ではない（<${slotEl.localName}>）`, slot);
    if (!SLOT_NAME.test(name)) add('S1', 'error', slotEl, `スロット名 "${name}" は英小文字・数字・ハイフンにする`, slot);
    if (names.has(name)) add('S1', 'error', slotEl, `スロット名 "${name}" が重複している`, slot);
    names.add(name);
    for (const el of allElements(slotEl)) slotSubtrees.add(el);

    // S2
    const clipId = clipRefId(slotEl.getAttribute('clip-path'));
    if (slotEl.getAttribute('clip-path') == null) add('S2', 'error', slotEl, `スロット "${name}" に clip-path 属性がない`, slot);
    else if (!clipId) add('S2', 'error', slotEl, `スロット "${name}" の clip-path が url(#id) の形でない`, slot);

    // S6
    if (slotEl.hasAttribute('mask')) add('S6', 'error', slotEl, `スロット "${name}" に mask がある`, slot);
    for (const a of ['opacity', 'filter']) {
      if (slotEl.hasAttribute(a)) add('S6', 'warning', slotEl, `スロット "${name}" の ${a} は編集画面では再現しない`, slot);
    }
    const slotT = parseTransform(slotEl.getAttribute('transform'));
    if (!slotT || !invert(slotT)) add('A2', 'error', slotEl, `スロット "${name}" の transform が読めないか、逆行列を持たない`, slot);
    guarded.push({ el: slotEl, slot });

    // S3
    const kids = elementChildren(slotEl);
    const image = kids.length === 1 && kids[0].localName === 'image' ? kids[0] : null;
    if (!image || hasNonWhitespaceText(slotEl)) {
      const what = kids.map((k) => `<${k.localName}>`).join(' ') || 'なし';
      add('S3', 'error', slotEl, `スロット "${name}" の子が <image> 1 つではない（${what}）`, slot);
    }

    // S4 / S5
    if (image) {
      guarded.push({ el: image, slot });
      for (const a of ['width', 'height']) {
        if (!isPlainNumber(image.getAttribute(a))) add('S4', 'error', image, `<image> の ${a} が単位なしの数値でない`, slot);
      }
      for (const a of ['x', 'y']) {
        const v = image.getAttribute(a);
        if (v != null && !isPlainNumber(v)) add('S4', 'error', image, `<image> の ${a} が単位なしの数値でない`, slot);
      }
      for (const a of ['clip-path', 'mask']) {
        if (image.hasAttribute(a)) add('S4', 'error', image, `<image> に ${a} がある（クリップはスロットの <g> に付ける）`, slot);
      }
      const t = parseTransform(image.getAttribute('transform'));
      if (!t || !invert(t)) add('S4', 'error', image, '<image> の transform が読めないか、逆行列を持たない', slot);
      const href = hrefOf(image).value;
      if (!isAllowedHref(href)) add('S5', 'error', image, `<image> の href が使えない形（${href == null ? 'なし' : href.slice(0, 40)}）`, slot);
    }

    // C1〜C3
    if (clipId) {
      const clip = byId.get(clipId);
      if (!clip || clip.localName !== 'clipPath') {
        add('C1', 'error', slotEl, `clip-path の参照先 #${clipId} が <clipPath> でない`, slot);
      } else {
        const units = clip.getAttribute('clipPathUnits');
        if (units != null && units !== 'userSpaceOnUse') add('C1', 'error', clip, `#${clipId} の clipPathUnits が userSpaceOnUse でない`, slot);
        if (clip.hasAttribute('clip-path')) add('C3', 'error', clip, `#${clipId} に clip-path がある（入れ子のクリップ）`, slot);
        const t = parseTransform(clip.getAttribute('transform'));
        if (!t || !invert(t)) add('C1', 'error', clip, `#${clipId} の transform が読めないか、逆行列を持たない`, slot);
        for (const child of elementChildren(clip)) {
          if (!CLIP_SHAPES.has(child.localName)) add('C2', 'error', child, `#${clipId} の子に <${child.localName}> がある（図形の要素だけにする）`, slot);
          if (child.hasAttribute('clip-path')) add('C3', 'error', child, `#${clipId} の子に clip-path がある`, slot);
          const ct = parseTransform(child.getAttribute('transform'));
          if (!ct || !invert(ct)) add('C2', 'error', child, `#${clipId} の子の transform が読めないか、逆行列を持たない`, slot);
        }
      }
    }

    // A1〜A4
    for (let anc = slotEl.parentNode; anc && anc.nodeType === 1 && anc !== root; anc = anc.parentNode) {
      if (anc.localName !== 'g') {
        const why = FORBIDDEN_ANCESTORS.has(anc.localName) ? '' : '（<g> 以外）';
        add('A1', 'error', anc, `スロット "${name}" の祖先に <${anc.localName}> がある${why}`, slot);
      }
      const t = parseTransform(anc.getAttribute('transform'));
      if (!t || !invert(t)) add('A2', 'error', anc, `スロット "${name}" の祖先の transform が読めないか、逆行列を持たない`, slot);
      for (const a of ['clip-path', 'mask']) {
        if (anc.hasAttribute(a)) add('A3', 'error', anc, `スロット "${name}" の祖先に ${a} がある`, slot);
      }
      for (const a of ['opacity', 'filter']) {
        if (anc.hasAttribute(a)) add('A4', 'warning', anc, `スロット "${name}" の祖先の ${a} は編集画面では再現しない`, slot);
      }
      guarded.push({ el: anc, slot });
    }
    if (slotEl.parentNode && slotEl.parentNode.nodeType === 1) guarded.push({ el: root, slot });
  }

  // D3
  for (const use of elements.filter((el) => el.localName === 'use')) {
    const href = hrefOf(use).value;
    const id = href && href.startsWith('#') ? href.slice(1) : null;
    const target = id ? byId.get(id) : null;
    if (target && slotSubtrees.has(target)) add('D3', 'error', use, `<use> がスロットの中身 #${id} を参照している`);
  }

  // D4: style 属性
  const seen = new Set();
  for (const { el, slot } of guarded) {
    if (seen.has(el)) continue;
    seen.add(el);
    const bad = declarationNames(el.getAttribute('style')).filter((p) => FORBIDDEN_CSS.has(p));
    if (bad.length) add('D4', 'error', el, `style 属性で ${bad.join(', ')} を当てている`, slot);
  }

  // D4: <style> の規則
  for (const styleEl of elements.filter((el) => el.localName === 'style')) {
    let rules;
    try {
      rules = parseCss(styleEl.textContent || '');
    } catch {
      add('D4', 'error', styleEl, '<style> を読めない');
      continue;
    }
    for (const rule of rules) {
      const bad = rule.properties.filter((p) => FORBIDDEN_CSS.has(p));
      if (!bad.length) continue;
      for (const sel of splitSelectors(rule.selector)) {
        const hits = [...seen].filter((el) => safeMatches(el, sel));
        for (const el of hits) {
          const slot = guarded.find((g) => g.el === el)?.slot;
          add('D4', 'error', el, `<style> の "${sel}" が ${bad.join(', ')} を当てている`, slot);
        }
      }
    }
  }

  return issues;
}

function splitSelectors(text) {
  return text.split(',').map((s) => s.trim()).filter(Boolean);
}

function safeMatches(el, selector) {
  try {
    return el.matches(selector);
  } catch {
    return false; // 読めないセレクタはどの要素にも当たらない（ブラウザも同じ）
  }
}

/** エラーがあるか */
export const hasErrors = (issues) => issues.some((i) => i.severity === 'error');
