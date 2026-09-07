'use strict';

// assets/icon.ico を生成する（`npm run icon`）。外部ライブラリなし（PNG は zlib で自前に書く）。
//
// 図柄: 藍色の角丸タイルに白い吹き出し、その中にプロンプト「›_」。「CLI と会話する」アプリの意匠で、
// agent-dashboard のアイコンとは別物にしてタスクバーで見分けられるようにする。
// 各サイズを 4×4 のスーパーサンプリングで描き、PNG 圧縮のまま ICO へ束ねる（Vista 以降の形式。
// electron-builder が要る 256px を含む）。

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const OUT = path.join(__dirname, '..', 'assets', 'icon.ico');
const SIZES = [16, 24, 32, 48, 64, 128, 256];
const SS = 4; // 1 辺あたりのサブサンプル数

// ---- 形（座標は 0..1 の正方形） -------------------------------------------------

function sdRoundRect(x, y, x0, y0, x1, y1, r) {
  const cx = (x0 + x1) / 2; const cy = (y0 + y1) / 2;
  const hx = (x1 - x0) / 2 - r; const hy = (y1 - y0) / 2 - r;
  const qx = Math.abs(x - cx) - hx; const qy = Math.abs(y - cy) - hy;
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}

function sdSegment(x, y, ax, ay, bx, by) {
  const px = x - ax; const py = y - ay; const dx = bx - ax; const dy = by - ay;
  const h = Math.min(1, Math.max(0, (px * dx + py * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - dx * h, py - dy * h);
}

function inTriangle(x, y, [ax, ay], [bx, by], [cx, cy]) {
  const s1 = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
  const s2 = (cx - bx) * (y - by) - (cy - by) * (x - bx);
  const s3 = (ax - cx) * (y - cy) - (ay - cy) * (x - cx);
  return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0);
}

const lerp = (a, b, t) => a + (b - a) * t;
const mix = (a, b, t) => a.map((v, i) => lerp(v, b[i], t));

// 1 点の色（RGBA 0..255。透明は [0,0,0,0]）
function shade(x, y, size) {
  // 小さいサイズでは線を太めにして潰れないようにする
  const small = size <= 32;
  const stroke = small ? 0.085 : 0.065;

  // 背景: 角丸タイル、左上→右下で藍から青紫へ
  if (sdRoundRect(x, y, 0.03, 0.03, 0.97, 0.97, 0.22) > 0) return [0, 0, 0, 0];
  const t = Math.min(1, Math.max(0, (x + y) / 2));
  const bg = mix([44, 52, 148], [96, 84, 224], t);

  // 吹き出し（角丸 + 左下のしっぽ）
  const bubble = sdRoundRect(x, y, 0.17, 0.20, 0.83, 0.70, 0.13) <= 0
    || inTriangle(x, y, [0.27, 0.62], [0.27, 0.85], [0.47, 0.68]);
  if (!bubble) return [...bg, 255];

  // プロンプト「›_」: 山形 2 本と下線。色は背景と同系の濃い藍
  const ink = [40, 44, 130];
  const chev = Math.min(
    sdSegment(x, y, 0.32, 0.34, 0.45, 0.45),
    sdSegment(x, y, 0.45, 0.45, 0.32, 0.56),
  ) <= stroke / 2;
  const bar = sdRoundRect(x, y, 0.52, 0.52, 0.70, 0.52 + stroke, stroke / 2) <= 0;
  if (chev || bar) return [...ink, 255];
  return [255, 255, 255, 255];
}

function raster(size) {
  const px = Buffer.alloc(size * size * 4);
  for (let j = 0; j < size; j += 1) {
    for (let i = 0; i < size; i += 1) {
      let r = 0; let g = 0; let b = 0; let a = 0;
      for (let sj = 0; sj < SS; sj += 1) {
        for (let si = 0; si < SS; si += 1) {
          const [cr, cg, cb, ca] = shade((i + (si + 0.5) / SS) / size, (j + (sj + 0.5) / SS) / size, size);
          // 事前乗算で平均してから戻す（縁の暗ずみを避ける）
          r += cr * ca; g += cg * ca; b += cb * ca; a += ca;
        }
      }
      const o = (j * size + i) * 4;
      if (a > 0) { px[o] = Math.round(r / a); px[o + 1] = Math.round(g / a); px[o + 2] = Math.round(b / a); }
      px[o + 3] = Math.round(a / (SS * SS));
    }
  }
  return px;
}

// ---- PNG / ICO ---------------------------------------------------------------

function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body) >>> 0);
  return Buffer.concat([len, body, crc]);
}

function png(size, px) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0; // 8bit RGBA
  const rows = Buffer.alloc((size * 4 + 1) * size);
  for (let j = 0; j < size; j += 1) {
    rows[j * (size * 4 + 1)] = 0; // filter: None
    px.copy(rows, j * (size * 4 + 1) + 1, j * size * 4, (j + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(rows, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(images.length, 4);
  const dir = [];
  let offset = 6 + 16 * images.length;
  for (const { size, data } of images) {
    const e = Buffer.alloc(16);
    e[0] = size >= 256 ? 0 : size; e[1] = size >= 256 ? 0 : size; e[2] = 0; e[3] = 0;
    e.writeUInt16LE(1, 4); e.writeUInt16LE(32, 6);
    e.writeUInt32LE(data.length, 8); e.writeUInt32LE(offset, 12);
    offset += data.length;
    dir.push(e);
  }
  return Buffer.concat([header, ...dir, ...images.map((i) => i.data)]);
}

function main() {
  const images = SIZES.map((size) => ({ size, data: png(size, raster(size)) }));
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, ico(images));
  // 確認用に最大サイズの PNG も並べて置く（README やストア用。ico と同じ図柄）
  fs.writeFileSync(OUT.replace(/\.ico$/, '.png'), images[images.length - 1].data);
  console.log(`icon: ${SIZES.join('/')}px → ${path.relative(path.join(__dirname, '..'), OUT)}`);
}

if (require.main === module) main();
module.exports = { SIZES, OUT, shade, raster, png, ico };
