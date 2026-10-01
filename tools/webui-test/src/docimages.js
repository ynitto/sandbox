'use strict';
// 仕様書の画像の整合。screenshot ステップの path: に置く画像（仕様書が貼っている画像）が今の画面と同じか、
// 仕様書のマークダウンが指している画像が実在するかを確かめる。

const fs = require('fs');
const path = require('path');

let comparator;
function getComparator() {
  if (comparator === undefined) {
    // @playwright/test の toHaveScreenshot と同じ比べ方（色の近さの許容つき）を使う。
    try { comparator = require('playwright-core/lib/utils').getComparator('image/png'); } catch (_) { comparator = null; }
  }
  return comparator;
}

function pngSize(buf) {
  return buf.length > 24 && buf.toString('ascii', 1, 4) === 'PNG' ? { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) } : null;
}

// 戻り値: { same, ratio, message, diff }。ratio は違う画素の割合（大きさが違うときは 1）、message は日本語の 1 行。
function compareImages(actual, expected, { maxDiffRatio = 0 } = {}) {
  if (actual.equals(expected)) return { same: true, ratio: 0 };
  const a = pngSize(actual);
  const e = pngSize(expected);
  if (!a || !e) return { same: false, ratio: 1, message: 'PNG として読めません' };
  if (a.width !== e.width || a.height !== e.height) {
    const cmp = getComparator();
    const r = cmp ? cmp(actual, expected, {}) : null;
    return { same: false, ratio: 1, message: `大きさが違います（${e.width}×${e.height} → ${a.width}×${a.height}）`, diff: r && r.diff };
  }
  const cmp = getComparator();
  if (!cmp) return { same: false, ratio: 1, message: '中身が違います' };
  const r = cmp(actual, expected, { maxDiffPixelRatio: maxDiffRatio, threshold: 0.2 });
  if (!r) return { same: true, ratio: 0 };
  const m = /(\d+) pixels/.exec(r.errorMessage || '');
  const count = m ? Number(m[1]) : e.width * e.height;
  const ratio = count / (e.width * e.height);
  return { same: false, ratio, message: `${count} 画素が違います（${(ratio * 100).toFixed(1)}%）`, diff: r.diff || null };
}

const MD_EXTS = ['.md', '.markdown', '.mdx'];
const IMG_LINK = /!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)|<img\s[^>]*?src\s*=\s*["']([^"']+)["']/gi;

function listMarkdown(roots) {
  const out = [];
  const walk = (p) => {
    let st;
    try { st = fs.statSync(p); } catch (_) { return; }
    if (st.isFile()) { if (MD_EXTS.includes(path.extname(p).toLowerCase())) out.push(p); return; }
    for (const name of fs.readdirSync(p).sort()) {
      if (name.startsWith('.') || name === 'node_modules' || name === 'webui-test-results') continue;
      walk(path.join(p, name));
    }
  };
  for (const r of roots) walk(r);
  return out;
}

// 仕様書（マークダウン）が貼っている画像を拾う。外部の URL は見ない。
// 戻り値: { docs: 件数, links: [{ doc, line, target, file, exists }] }
function scanDocs(roots) {
  const docs = listMarkdown(roots);
  const links = [];
  for (const doc of docs) {
    let fenced = false;
    fs.readFileSync(doc, 'utf8').split(/\r?\n/).forEach((text, i) => {
      if (/^\s*(```|~~~)/.test(text)) { fenced = !fenced; return; }
      if (fenced) return;
      for (const m of text.replace(/`[^`]*`/g, '').matchAll(IMG_LINK)) {
        const target = m[1] || m[2];
        if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('/') || target.startsWith('#')) continue;
        let rel;
        try { rel = decodeURI(target.split(/[?#]/)[0]); } catch (_) { rel = target.split(/[?#]/)[0]; }
        const file = path.resolve(path.dirname(doc), rel);
        links.push({ doc, line: i + 1, target, file, exists: fs.existsSync(file) });
      }
    });
  }
  return { docs: docs.length, links };
}

module.exports = { compareImages, scanDocs };
