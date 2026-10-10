'use strict';

// electron.test.js から Electron で起動される。3 形式を同時に撮り、結果を JSON で標準出力に書く。

const { app, nativeImage } = require('electron');
const { renderPreview } = require('../src');
const { docx, xlsx, pptx } = require('./helpers');

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
    const jpeg = await renderPreview(inputs.pptx, { scale: 0.25, format: 'jpeg' });
    out.jpeg = { mime: jpeg.mime, width: jpeg.width, height: jpeg.height, soi: jpeg.data.subarray(0, 2).toString('hex') };
    process.stdout.write(`RESULT ${JSON.stringify(out)}\n`);
    app.exit(0);
  } catch (err) {
    process.stdout.write(`ERROR ${err.code || ''} ${err.stack}\n`);
    app.exit(1);
  }
});
