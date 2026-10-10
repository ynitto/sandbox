'use strict';

// OOXML を読むだけの小さな XML パーサ。DTD と外部実体は読まない（XXE を起こさない）。
// 要素は { name, local, attrs, children } で、children には要素と文字列が混ざる。
// 接頭辞は文書ごとに変わりうるので、要素は local（接頭辞を除いた名前）で探す。
// 関係（relationships）名前空間の属性だけは、接頭辞が何であっても attrs['r:xxx'] でも引けるようにする。

const { OfficePreviewError } = require('./errors');

const ENTITIES = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };

function decode(s) {
  if (s.indexOf('&') < 0) return s;
  return s.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g, (m, e) => {
    if (e[0] === '#') {
      const cp = e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(cp) && cp >= 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : '';
    }
    return ENTITIES[e] ?? m;
  });
}

const ATTR_RE = /([^\s=/>]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
const REL_NS = /\/relationships$/;

function parseXml(text) {
  if (typeof text !== 'string') throw new OfficePreviewError('BROKEN', 'XML が無い');
  const root = { name: '#document', local: '#document', attrs: {}, children: [] };
  const stack = [root];
  const nsStack = [{}];
  let i = 0;
  const n = text.length;
  while (i < n) {
    const lt = text.indexOf('<', i);
    if (lt < 0) { pushText(stack, text.slice(i)); break; }
    if (lt > i) pushText(stack, text.slice(i, lt));
    if (text.startsWith('<?', lt)) { i = end(text, '?>', lt) + 2; continue; }
    if (text.startsWith('<!--', lt)) { i = end(text, '-->', lt) + 3; continue; }
    if (text.startsWith('<![CDATA[', lt)) {
      const e = end(text, ']]>', lt);
      stack[stack.length - 1].children.push(text.slice(lt + 9, e));
      i = e + 3;
      continue;
    }
    if (text.startsWith('<!', lt)) { i = skipDecl(text, lt); continue; }
    if (text[lt + 1] === '/') {
      const e = end(text, '>', lt);
      const name = text.slice(lt + 2, e).trim();
      const top = stack[stack.length - 1];
      if (stack.length === 1 || top.name !== name) throw new OfficePreviewError('BROKEN', `XML の閉じタグが合わない: ${name}`);
      stack.pop();
      nsStack.pop();
      i = e + 1;
      continue;
    }
    const e = tagEnd(text, lt);
    let body = text.slice(lt + 1, e);
    const selfClose = body.endsWith('/');
    if (selfClose) body = body.slice(0, -1);
    const sp = body.search(/\s/);
    const name = sp < 0 ? body : body.slice(0, sp);
    const attrs = {};
    const ns = Object.create(nsStack[nsStack.length - 1]);
    if (sp >= 0) {
      ATTR_RE.lastIndex = sp;
      let m;
      while ((m = ATTR_RE.exec(body))) {
        const value = decode(m[3] ?? m[4] ?? '');
        attrs[m[1]] = value;
        if (m[1].startsWith('xmlns:')) ns[m[1].slice(6)] = value;
      }
      for (const key of Object.keys(attrs)) {
        const c = key.indexOf(':');
        if (c < 0 || key.startsWith('xmlns')) continue;
        const prefix = key.slice(0, c);
        if (prefix !== 'r' && REL_NS.test(ns[prefix] || '')) attrs[`r:${key.slice(c + 1)}`] ??= attrs[key];
      }
    }
    const c = name.indexOf(':');
    const node = { name, local: c < 0 ? name : name.slice(c + 1), attrs, children: [] };
    stack[stack.length - 1].children.push(node);
    if (!selfClose) { stack.push(node); nsStack.push(ns); }
    i = e + 1;
  }
  if (stack.length !== 1) throw new OfficePreviewError('BROKEN', `XML が途中で終わっている: ${stack[stack.length - 1].name}`);
  const top = root.children.find((x) => typeof x !== 'string');
  if (!top) throw new OfficePreviewError('BROKEN', 'XML に要素が無い');
  return top;
}

function pushText(stack, s) {
  if (stack.length === 1) return; // 要素の外の空白
  stack[stack.length - 1].children.push(decode(s));
}

function end(text, token, from) {
  const e = text.indexOf(token, from);
  if (e < 0) throw new OfficePreviewError('BROKEN', 'XML が途中で終わっている');
  return e;
}

// 属性値の中の > で切らないように、引用符を数えながら終わりを探す
function tagEnd(text, from) {
  let q = '';
  for (let i = from + 1; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === q) q = ''; } else if (ch === '"' || ch === "'") q = ch;
    else if (ch === '>') return i;
  }
  throw new OfficePreviewError('BROKEN', 'XML が途中で終わっている');
}

// <!DOCTYPE ... [ ... ]> は読み飛ばす（実体の定義は使わない）
function skipDecl(text, from) {
  let depth = 0;
  for (let i = from + 2; i < text.length; i++) {
    if (text[i] === '[') depth++;
    else if (text[i] === ']') depth--;
    else if (text[i] === '>' && depth <= 0) return i + 1;
  }
  throw new OfficePreviewError('BROKEN', 'XML が途中で終わっている');
}

// ---- たどる道具 -------------------------------------------------------------

function isEl(x) { return x && typeof x === 'object'; }

function kids(node, local) {
  if (!node) return [];
  const out = [];
  for (const c of node.children) if (isEl(c) && (!local || c.local === local)) out.push(c);
  return out;
}

function kid(node, local) {
  if (!node) return null;
  for (const c of node.children) if (isEl(c) && c.local === local) return c;
  return null;
}

// kid の連鎖。path('a', 'b', 'c') のように順にたどる
function path(node, ...locals) {
  let cur = node;
  for (const l of locals) { cur = kid(cur, l); if (!cur) return null; }
  return cur;
}

// 子孫を深さ優先で探す（最初の 1 つ）
function find(node, local) {
  if (!node) return null;
  for (const c of node.children) {
    if (!isEl(c)) continue;
    if (c.local === local) return c;
    const hit = find(c, local);
    if (hit) return hit;
  }
  return null;
}

function findAll(node, local, out = []) {
  if (!node) return out;
  for (const c of node.children) {
    if (!isEl(c)) continue;
    if (c.local === local) out.push(c);
    findAll(c, local, out);
  }
  return out;
}

// 属性を接頭辞なしの名前でも引く（w:val と val のどちらでも）
function attr(node, name) {
  if (!node) return undefined;
  const a = node.attrs;
  if (name in a) return a[name];
  for (const key of Object.keys(a)) {
    const c = key.indexOf(':');
    if (c >= 0 && key.slice(c + 1) === name && !key.startsWith('xmlns') && !key.startsWith('r:')) return a[key];
  }
  return undefined;
}

function textOf(node) {
  if (!node) return '';
  let s = '';
  for (const c of node.children) s += isEl(c) ? textOf(c) : c;
  return s;
}

module.exports = { parseXml, kids, kid, path, find, findAll, attr, textOf, isEl };
