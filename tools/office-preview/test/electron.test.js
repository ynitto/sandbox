'use strict';

// Electron を実際に起動して画像を撮る。electron のバイナリか表示先が無い環境（CI）では飛ばす。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { renderPreview } = require('../src');

function electronBinary() {
  try {
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

test('Electron の外で renderPreview を呼ぶと NO_ELECTRON', async () => {
  await assert.rejects(renderPreview(Buffer.alloc(0)), { code: 'NO_ELECTRON' });
});

test('実機: docx / xlsx / pptx / pdf を同時に PNG にし、JPEG でも出せる', (t) => {
  const binary = electronBinary();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い（xvfb-run で動かす）'); return; }
  const args = [path.join(__dirname, 'electron-main.js')];
  if (process.platform === 'linux') args.unshift('--no-sandbox');
  const run = spawnSync(binary, args, { encoding: 'utf8', timeout: 90000, env: { ...process.env, ELECTRON_ENABLE_LOGGING: '' } });
  const line = (run.stdout || '').split('\n').find((l) => l.startsWith('RESULT ') || l.startsWith('ERROR '));
  assert.ok(line, `結果が無い: ${run.stdout}\n${run.stderr}`);
  assert.ok(line.startsWith('RESULT '), line);
  const r = JSON.parse(line.slice(7));
  for (const type of ['docx', 'xlsx', 'pptx']) {
    assert.strictEqual(r[type].mime, 'image/png');
    assert.strictEqual(r[type].signature, '89504e470d0a1a0a', `${type} は PNG`);
    assert.strictEqual(r[type].width, 400);
    assert.strictEqual(r[type].pngWidth, 400);
    assert.strictEqual(r[type].pngHeight, r[type].height);
  }
  assert.strictEqual(r.docx.height, Math.round(400 * 1123 / 794)); // A4 の縦横比
  assert.strictEqual(r.pptx.height, 225); // 16:9
  assert.strictEqual(r.pptx.corner, '#102030'); // マスターの背景が描かれている
  assert.strictEqual(r.docx.corner, '#FFFFFF');
  assert.deepStrictEqual(r.jpeg, { mime: 'image/jpeg', width: 320, height: 180, soi: 'ffd8' });
  assert.deepStrictEqual(r.pdf, [
    { type: 'pdf', page: 0, width: 400, height: 200, center: '#FF0000' },
    { type: 'pdf', page: 1, width: 300, height: 600, center: '#0000FF' },
  ]);
  assert.deepStrictEqual(r.pdfErrors, ['BROKEN']);
});
