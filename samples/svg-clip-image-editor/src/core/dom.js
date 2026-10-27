// 解析した SVG 文書を辿る小道具。ブラウザの DOM と jsdom の両方で動くものだけを使う。

export const XLINK = 'http://www.w3.org/1999/xlink';

export function elementChildren(el) {
  return Array.from(el.childNodes).filter((n) => n.nodeType === 1);
}

/** 要素を除いた子（空白でない文字・CDATA）があるか */
export function hasNonWhitespaceText(el) {
  return Array.from(el.childNodes).some((n) => (n.nodeType === 3 || n.nodeType === 4) && n.nodeValue.trim() !== '');
}

/** ルートから el までの子インデックス列（要素だけを数える） */
export function pathOf(el) {
  const path = [];
  let cur = el;
  while (cur.parentNode && cur.parentNode.nodeType === 1) {
    path.unshift(elementChildren(cur.parentNode).indexOf(cur));
    cur = cur.parentNode;
  }
  return path;
}

export function elementAtPath(root, path) {
  let cur = root;
  for (const i of path) {
    cur = elementChildren(cur)[i];
    if (!cur) return null;
  }
  return cur;
}

/** 人が読む位置: '/svg/g[2]/image[1]'（同名の兄弟の中での 1 始まりの番号） */
export function locationOf(el) {
  const parts = [];
  let cur = el;
  while (cur && cur.nodeType === 1) {
    const parent = cur.parentNode;
    if (parent && parent.nodeType === 1) {
      const same = elementChildren(parent).filter((c) => c.localName === cur.localName);
      parts.unshift(`${cur.localName}[${same.indexOf(cur) + 1}]`);
    } else {
      parts.unshift(cur.localName);
    }
    cur = parent;
  }
  return '/' + parts.join('/');
}

export function hrefOf(el) {
  if (el.hasAttribute('href')) return { value: el.getAttribute('href'), attr: 'href' };
  const x = el.getAttributeNS(XLINK, 'href');
  if (x != null) return { value: x, attr: 'xlink:href' };
  return { value: null, attr: 'href' };
}

/** 文書順にすべての要素 */
export function allElements(root) {
  const out = [];
  const walk = (el) => {
    out.push(el);
    for (const c of elementChildren(el)) walk(c);
  };
  walk(root);
  return out;
}

const CLIP_URL = /^\s*url\(\s*(['"]?)#([^'")\s]+)\1\s*\)\s*$/;

/** clip-path="url(#id)" の id。書式が違えば null */
export function clipRefId(value) {
  const m = CLIP_URL.exec(value ?? '');
  return m ? m[2] : null;
}

const NUMERIC = /^\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\s*$/;
export const isPlainNumber = (v) => v != null && NUMERIC.test(v);

/** viewBox="x y w h" → Rect。読めない・幅高さが正でなければ null */
export function parseViewBox(value) {
  if (value == null) return null;
  const n = value.trim().split(/[\s,]+/).map(Number);
  if (n.length !== 4 || n.some((v) => !Number.isFinite(v)) || !(n[2] > 0 && n[3] > 0)) return null;
  return { x: n[0], y: n[1], width: n[2], height: n[3] };
}
