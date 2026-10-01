'use strict';
// 2 枚の画像が同じ画面かを比べる（webui-test check が前回の画面と比べるのに使う）。

const fs = require('fs');
const path = require('path');

// 画素の比べ方は Playwright の toHaveScreenshot と同じ pixelmatch（色の近さの許容 0.2）。
const { PNG } = require('pngjs');
const pixelmatch = require('pixelmatch');

function readPng(buf) {
  try { return PNG.sync.read(buf); } catch (_) { return null; }
}

// 戻り値: { same, ratio, message, diff }。ratio は違う画素の割合（大きさが違うときは 1）、message は日本語の 1 行。
function compareImages(actual, expected, { maxDiffRatio = 0 } = {}) {
  if (actual.equals(expected)) return { same: true, ratio: 0 };
  const a = readPng(actual);
  const e = readPng(expected);
  if (!a || !e) return { same: false, ratio: 1, message: 'PNG として読めません' };
  if (a.width !== e.width || a.height !== e.height) {
    return { same: false, ratio: 1, message: `大きさが違います（${e.width}×${e.height} → ${a.width}×${a.height}）` };
  }
  const diff = new PNG({ width: e.width, height: e.height });
  const count = pixelmatch(e.data, a.data, diff.data, e.width, e.height, { threshold: 0.2 });
  const ratio = count / (e.width * e.height);
  if (count <= Math.floor(e.width * e.height * maxDiffRatio)) return { same: true, ratio };
  return { same: false, ratio, message: `${count} 画素が違います（${(ratio * 100).toFixed(1)}%）`, diff: PNG.sync.write(diff) };
}

module.exports = { compareImages };
