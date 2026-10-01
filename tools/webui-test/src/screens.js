'use strict';
// 前回の画面と比べる（webui-test check）。仕様書には依存しない。
//
// 結果の置き場の screens/ に、スクリーンショットごとの最新の画像と、これまでの版の sha256（index.json）を持つ。
// 比べ方は軽くする: まずバイト列で比べ、違うときだけ画素で比べる。画素で同じとみなしたときは前回の画像を残す
// （同じ画面なら同じファイル・同じ sha256 のままにして、受け取る側がバイト列だけで比べられるようにする）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { compareImages } = require('./images');

const MAX_HISTORY = 20;

const sha256 = (buf) => crypto.createHash('sha256').update(buf).digest('hex');
const safe = (s) => String(s).replace(/[<>:"\\|?*\s]+/g, '_');

function caseKey(file, c) {
  const stem = path.basename(file).replace(/\.(ya?ml|json)$/i, '');
  return `${stem}/${c.variant ? `${c.id}[${c.variant}]` : c.id}`;
}

// report の各スクリーンショットを前回と比べ、screens/ を今の画面に更新する。
// 戻り値: [{ id, file, suite, title, status: same|changed|new|removed, path, sha256, history, previous?, diff?, ratio? }]
function compareScreens(report, { outDir, screensDir, maxDiffRatio = 0 }) {
  fs.mkdirSync(screensDir, { recursive: true });
  const indexFile = path.join(screensDir, 'index.json');
  let index = {};
  try { index = JSON.parse(fs.readFileSync(indexFile, 'utf8')); } catch (_) { /* 初めて */ }
  const out = [];
  const seen = new Set();
  const ranFiles = new Set();
  for (const s of report.suites) {
    if (!s.file) continue;
    const file = path.resolve(s.file);
    ranFiles.add(file);
    for (const c of s.cases) {
      if (c.status === 'skipped') continue;
      const names = new Map();
      for (const sh of c.screenshots || []) {
        if (!sh.explicit) continue; // 比べるのは screenshot ステップの画面だけ（操作ごと・失敗時の画像は見ない）
        const n = (names.get(sh.name) || 0) + 1;
        names.set(sh.name, n);
        const id = `${caseKey(file, c)}/${sh.name}${n > 1 ? `-${n}` : ''}`;
        seen.add(id);
        const shot = fs.readFileSync(path.join(outDir, sh.file));
        const dest = path.join(screensDir, ...id.split('/').map(safe)) + '.png';
        const prev = index[id];
        const entry = { id, file, suite: s.suite, title: `${s.suite} ${c.title}${c.variant ? ` [${c.variant}]` : ''}（${sh.name}）` };
        if (!prev || !fs.existsSync(dest)) {
          entry.status = 'new';
        } else {
          const old = fs.readFileSync(dest);
          const cmp = shot.equals(old) ? { same: true } : compareImages(shot, old, { maxDiffRatio });
          if (cmp.same) {
            entry.status = 'same';
          } else {
            entry.status = 'changed';
            entry.ratio = cmp.ratio;
            entry.message = cmp.message;
            const previous = path.join(outDir, 'previous', ...id.split('/').map(safe)) + '.png';
            fs.mkdirSync(path.dirname(previous), { recursive: true });
            fs.writeFileSync(previous, old);
            entry.previous = previous;
            if (cmp.diff) {
              entry.diff = previous.replace(/\.png$/, '.diff.png');
              fs.writeFileSync(entry.diff, cmp.diff);
            }
          }
        }
        if (entry.status !== 'same') {
          fs.mkdirSync(path.dirname(dest), { recursive: true });
          fs.writeFileSync(dest, shot);
        }
        const hash = entry.status === 'same' ? prev.sha256 : sha256(shot);
        const history = [hash, ...((prev && prev.history) || []).filter((h) => h !== hash)].slice(0, MAX_HISTORY);
        index[id] = { sha256: hash, history, file };
        Object.assign(entry, { path: dest, sha256: hash, history });
        out.push(entry);
      }
    }
  }
  // 今回動かしたケースファイルで、撮らなくなった画面
  for (const [id, prev] of Object.entries(index)) {
    if (seen.has(id) || !ranFiles.has(prev.file)) continue;
    out.push({ id, file: prev.file, status: 'removed', sha256: prev.sha256, history: prev.history || [prev.sha256] });
    fs.rmSync(path.join(screensDir, ...id.split('/').map(safe)) + '.png', { force: true });
    delete index[id];
  }
  fs.writeFileSync(indexFile, JSON.stringify(index, null, 2) + '\n');
  return out;
}

module.exports = { compareScreens, caseKey };
