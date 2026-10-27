// 出力。元の文字列のうち、各スロットの <image> の開始タグの transform 属性だけを書き換える。
// DOM を文字列に戻す（XMLSerializer）と属性の引用符・空白・宣言が変わるので、文字列を直接継ぎ当てる。

import { elementAtPath, elementChildren } from './dom.js';
import { equals, formatDecomposed, formatMatrix, IDENTITY } from './matrix.js';

/**
 * @param {object} model parseClipDocument の model
 * @param {Record<string, number[]>} transforms スロット名 → 行列（ないスロットは触らない）
 * @param {{ precision?: number, outputTransform?: 'matrix' | 'decomposed' }} [opts]
 */
export function serialize(model, transforms, opts = {}) {
  const precision = opts.precision ?? 3;
  const format = opts.outputTransform === 'decomposed' ? formatDecomposed : formatMatrix;
  const root = model.doc.documentElement;
  const images = Array.from(model.doc.getElementsByTagNameNS('*', 'image'));
  const edits = new Map(); // 文書順の <image> の番号 → 新しい transform（null は消す）
  for (const slot of model.slots) {
    const M = transforms?.[slot.name];
    // 読み込み時と同じなら元の文字のまま残す
    if (!M || equals(M, slot.M0, 1e-12)) continue;
    const image = elementChildren(elementAtPath(root, slot.path))[0];
    const value = equals(M, IDENTITY, 1e-12) ? null : format(M, precision);
    edits.set(images.indexOf(image), value);
  }
  if (!edits.size) return model.source;
  return patchImageTags(model.source, edits);
}

/** 文字列の中の n 番目の <image> 開始タグの transform を書き換える */
export function patchImageTags(source, edits) {
  let out = '';
  let pos = 0;
  let index = -1;
  const tagStart = /<(?:[A-Za-z_][\w.-]*:)?image(?=[\s/>])/y;
  let i = 0;
  while (i < source.length) {
    const lt = source.indexOf('<', i);
    if (lt < 0) break;
    if (source.startsWith('<!--', lt)) {
      i = endOf(source, '-->', lt);
      continue;
    }
    if (source.startsWith('<![CDATA[', lt)) {
      i = endOf(source, ']]>', lt);
      continue;
    }
    if (source.startsWith('<?', lt)) {
      i = endOf(source, '?>', lt);
      continue;
    }
    if (source.startsWith('<!', lt)) {
      i = skipDeclaration(source, lt);
      continue;
    }
    const end = tagEnd(source, lt);
    tagStart.lastIndex = lt;
    if (tagStart.test(source)) {
      index++;
      if (edits.has(index)) {
        out += source.slice(pos, lt) + setAttribute(source.slice(lt, end), 'transform', edits.get(index));
        pos = end;
      }
    }
    i = end;
  }
  return out + source.slice(pos);
}

function endOf(source, marker, from) {
  const j = source.indexOf(marker, from);
  return j < 0 ? source.length : j + marker.length;
}

// <!DOCTYPE …[ … ]> の内部サブセットも飛ばす
function skipDeclaration(source, lt) {
  let depth = 0;
  for (let j = lt + 2; j < source.length; j++) {
    const ch = source[j];
    if (ch === '[') depth++;
    else if (ch === ']') depth--;
    else if (ch === '>' && depth <= 0) return j + 1;
  }
  return source.length;
}

// 引用符の中の > を飛ばして、タグの終わり（> の次）を返す
function tagEnd(source, lt) {
  let quote = null;
  for (let j = lt + 1; j < source.length; j++) {
    const ch = source[j];
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === '>') {
      return j + 1;
    }
  }
  return source.length;
}

/** 開始タグの文字列の中で属性を書き換える（null なら消す、なければ足す） */
export function setAttribute(tag, name, value) {
  const attr = new RegExp(`(\\s+)${name}\\s*=\\s*("[^"]*"|'[^']*')`);
  const m = attr.exec(tag);
  if (m) {
    if (value == null) return tag.slice(0, m.index) + tag.slice(m.index + m[0].length);
    const q = m[2][0];
    return tag.slice(0, m.index) + `${m[1]}${name}=${q}${value}${q}` + tag.slice(m.index + m[0].length);
  }
  if (value == null) return tag;
  const close = /\s*\/?>$/.exec(tag);
  return tag.slice(0, close.index) + ` ${name}="${value}"` + tag.slice(close.index);
}
