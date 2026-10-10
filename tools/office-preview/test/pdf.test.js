'use strict';

// PDF（Electron の PDF ビューアに描かせる部分のうち、node だけで確かめられるところ）

const { test } = require('node:test');
const assert = require('node:assert');
const { isPdf, guessPageSize, findPageBox, pdfViewerUrl } = require('../src/pdf');
const { toHtml, readEmbeddedThumbnail, supports } = require('../src');
const { pdf } = require('./helpers');

// ビューアの画面を模した BGRA の画像。背景 #282828 に、矩形を塗っていく
function screen(width, height, rects) {
  const bmp = Buffer.alloc(width * height * 4);
  const fill = (x0, y0, w, h, [r, g, b]) => {
    for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) {
      const i = (y * width + x) * 4;
      bmp[i] = b; bmp[i + 1] = g; bmp[i + 2] = r; bmp[i + 3] = 255;
    }
  };
  fill(0, 0, width, height, [40, 40, 40]);
  for (const r of rects) fill(...r);
  return bmp;
}

test('PDF を見分け、MediaBox と Rotate からページの大きさを推す', () => {
  const doc = pdf([{ width: 200, height: 100, rgb: [1, 0, 0] }]);
  assert.ok(isPdf(doc));
  assert.ok(isPdf(Buffer.concat([Buffer.from('junk\n'), doc])));
  assert.ok(!isPdf(Buffer.from('PK\x03\x04')));
  assert.deepStrictEqual(guessPageSize(doc), { width: 200, height: 100 });
  assert.deepStrictEqual(guessPageSize(Buffer.from('%PDF-1.4 /MediaBox [0 0 595.3 841.9] /Rotate 90')), { width: 841.9, height: 595.3 });
  assert.strictEqual(guessPageSize(Buffer.from('%PDF-1.5 (compressed)')), null);
  assert.ok(supports('a.PDF'));
  assert.strictEqual(pdfViewerUrl('file:///t/page.pdf', { page: 2 }), 'file:///t/page.pdf#page=3&toolbar=0&navpanes=0&scrollbar=0&view=Fit');
});

test('PDF: 1 ページ目だけを切り出し、影とスクロールバーは含めない（黒いページも見分ける）', () => {
  const W = 160, H = 120;
  const bmp = screen(W, H, [
    [38, 0, 84, 3, [37, 37, 37]], // 上端の影（背景とほとんど同じ）
    [60, 0, 3, 1, [52, 52, 52]], // 数画素だけ背景と違う行
    [38, 9, 84, 45, [23, 23, 23]], // 影（背景より少し暗い無彩色）
    [40, 10, 80, 40, [255, 255, 255]], // 1 ページ目
    [40, 60, 80, 40, [0, 0, 0]], // 下に見えている 2 ページ目
    [148, 0, 12, H, [250, 250, 250]], // スクロールバー
  ]);
  assert.deepStrictEqual(findPageBox(bmp, W, H), { x: 40, y: 10, width: 80, height: 40, square: true });
  const dark = screen(W, H, [[38, 1, 84, 45, [23, 23, 23]], [40, 2, 80, 40, [0, 0, 0]], [148, 0, 12, H, [250, 250, 250]]]);
  assert.deepStrictEqual(findPageBox(dark, W, H), { x: 40, y: 2, width: 80, height: 40, square: true });
});

test('PDF: 角の丸い箱（パスワードの入力・読み込み失敗の案内）はページとみなさない', () => {
  const W = 90, H = 40;
  const bmp = screen(W, H, [[10, 10, 40, 20, [255, 255, 255]]]);
  // 四隅を背景に戻して角を丸くする
  for (const [x, y] of [[10, 10], [49, 10], [10, 29], [49, 29]]) {
    const i = (y * W + x) * 4;
    bmp[i] = 40; bmp[i + 1] = 40; bmp[i + 2] = 40;
  }
  const box = findPageBox(bmp, W, H);
  assert.strictEqual(box.square, false);
  assert.strictEqual(findPageBox(screen(W, H, []), W, H), null);
});

test('PDF は HTML にせず、埋め込みのサムネイルも無い', async () => {
  const doc = pdf([{ width: 200, height: 100, rgb: [1, 0, 0] }]);
  await assert.rejects(toHtml(doc), { code: 'UNSUPPORTED' });
  assert.strictEqual(await readEmbeddedThumbnail(doc), null);
});
