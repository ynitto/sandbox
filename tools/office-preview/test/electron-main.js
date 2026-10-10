'use strict';

// electron.test.js から Electron で起動される。4 形式を撮り、結果を JSON で標準出力に書く。

const { app, nativeImage } = require('electron');
const { renderPreview } = require('../src');
const { docx, xlsx, pptx, pdf } = require('./helpers');

app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});

// 左上から (x, y) の色を #RRGGBB で返す（toBitmap は BGRA）
function pixel(png, x, y) {
  const img = nativeImage.createFromBuffer(png);
  const { width } = img.getSize();
  const bmp = img.toBitmap();
  const i = (y * width + x) * 4;
  return `#${[bmp[i + 2], bmp[i + 1], bmp[i]].map((v) => v.toString(16).padStart(2, '0')).join('')}`.toUpperCase();
}

app.whenReady().then(async () => {
  const inputs = {
    docx: docx('<w:p><w:r><w:t>本文</w:t></w:r></w:p><w:background w:color="FFFFFF"/>'),
    xlsx: xlsx({ sheets: [['S', '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>値</t></is></c></row></sheetData>']] }),
    pptx: pptx(['<p:sp><p:nvSpPr><p:cNvPr id="2" name="t"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>題</a:t></a:r></a:p></p:txBody></p:sp>']),
  };
  const out = {};
  try {
    await Promise.all(Object.entries(inputs).map(async ([type, buf]) => {
      const r = await renderPreview(buf, { width: 400 });
      out[type] = {
        mime: r.mime,
        width: r.width,
        height: r.height,
        signature: r.data.subarray(0, 8).toString('hex'),
        pngWidth: r.data.readUInt32BE(16),
        pngHeight: r.data.readUInt32BE(20),
        corner: pixel(r.data, 2, r.height - 3),
      };
    }));
    // PDF は Electron の PDF ビューアで描く。1 ページ目は横長の赤、2 ページ目は縦長の青
    const doc = pdf([{ width: 200, height: 100, rgb: [1, 0, 0] }, { width: 100, height: 200, rgb: [0, 0, 1] }]);
    const [p1, p2] = await Promise.all([renderPreview(doc, { width: 400 }), renderPreview(doc, { width: 300, page: 1 })]);
    out.pdf = [p1, p2].map((r) => ({ type: r.type, page: r.page, width: r.width, height: r.height, center: pixel(r.data, Math.floor(r.width / 2), Math.floor(r.height / 2)) }));
    out.pdfErrors = [];
    for (const bad of [Buffer.from(`%PDF-1.7\n${'x'.repeat(500)}`)]) {
      try { await renderPreview(bad, { width: 200 }); out.pdfErrors.push('no error'); } catch (err) { out.pdfErrors.push(err.code); }
    }
    const jpeg = await renderPreview(inputs.pptx, { scale: 0.25, format: 'jpeg' });
    out.jpeg = { mime: jpeg.mime, width: jpeg.width, height: jpeg.height, soi: jpeg.data.subarray(0, 2).toString('hex') };
    process.stdout.write(`RESULT ${JSON.stringify(out)}\n`);
    app.exit(0);
  } catch (err) {
    process.stdout.write(`ERROR ${err.code || ''} ${err.stack}\n`);
    app.exit(1);
  }
});
