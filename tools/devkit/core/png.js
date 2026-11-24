"use strict";

const zlib = require("node:zlib");
const { constants } = require("node:buffer");

const PNG_SIGNATURE = Buffer.from("89504e470d0a1a0a", "hex");
const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

// zlib.crc32 is available from Node 22.2. Keep compatibility with Node 18.
const crcTable = Array.from({ length: 256 }, (_, value) => {
  for (let i = 0; i < 8; i++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  return value;
});
const crc32 = zlib.crc32 ?? ((bytes) => {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255];
  return (crc ^ 0xffffffff) >>> 0;
});

function imageLength(width, height, channels) {
  const length = width * height * channels;
  if (!Number.isInteger(width) || !Number.isInteger(height) ||
      width < 1 || height < 1 || width > 0x7fffffff || height > 0x7fffffff ||
      length > constants.MAX_LENGTH || !Number.isSafeInteger(length)) {
    throw new Error("画像の寸法が不正か、大きすぎます");
  }
  return length;
}

function paeth(left, up, diagonal) {
  const estimate = left + up - diagonal;
  const a = Math.abs(estimate - left);
  const b = Math.abs(estimate - up);
  const c = Math.abs(estimate - diagonal);
  if (a <= b && a <= c) return left;
  return b <= c ? up : diagonal;
}

const FILTERS = [
  () => 0,
  (left) => left,
  (_left, up) => up,
  (left, up) => (left + up) >>> 1,
  paeth,
];

function decodePng(buffer) {
  if (!buffer.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error("PNG シグネチャが不正です");

  let header, palette, transparency;
  const compressed = [];
  let ended = false;
  for (let offset = 8; offset + 12 <= buffer.length;) {
    const length = buffer.readUInt32BE(offset);
    const end = offset + 8 + length;
    if (end + 4 > buffer.length) throw new Error("PNG チャンクが途中で切れています");
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    if (crc32(buffer.subarray(offset + 4, end)) !== buffer.readUInt32BE(end)) {
      throw new Error(`PNG CRC が一致しません (${type})`);
    }
    const bytes = buffer.subarray(offset + 8, end);
    if (offset === 8 && type !== "IHDR") throw new Error("PNG の先頭に IHDR がありません");
    if (type === "IHDR") {
      if (header || length !== 13) throw new Error("PNG IHDR が不正です");
      header = bytes;
    }
    if (type === "PLTE") palette = bytes;
    if (type === "tRNS") transparency = bytes;
    if (type === "IDAT") compressed.push(bytes);
    offset = end + 4;
    if (type === "IEND") {
      if (length !== 0 || offset !== buffer.length) throw new Error("PNG IEND が不正です");
      ended = true;
      break;
    }
  }
  if (!header || !compressed.length || !ended) throw new Error("PNG の必須チャンクがありません");
  const width = header.readUInt32BE(0);
  const height = header.readUInt32BE(4);
  const [depth, colorType, compression, filterMethod, interlace] = header.subarray(8);
  if (interlace !== 0) throw new Error("インターレース PNG (Adam7) には対応していません");
  const channels = CHANNELS[colorType];
  if (!channels || ![8, 16].includes(depth) || (colorType === 3 && depth !== 8) ||
      compression !== 0 || filterMethod !== 0) throw new Error("未対応の PNG 形式です");
  if (colorType === 3 && (!palette?.length || palette.length % 3 || palette.length > 768)) {
    throw new Error("PNG パレットが不正です");
  }
  if (transparency && ((colorType === 0 && transparency.length !== 2) ||
      (colorType === 2 && transparency.length !== 6) ||
      (colorType === 3 && transparency.length > palette.length / 3))) {
    throw new Error("PNG tRNS が不正です");
  }

  const outputLength = imageLength(width, height, 4);
  const sampleBytes = depth / 8;
  const bytesPerPixel = channels * sampleBytes;
  const rowLength = width * bytesPerPixel;
  const rawLength = (rowLength + 1) * height;
  let raw;
  try {
    raw = zlib.inflateSync(Buffer.concat(compressed), { maxOutputLength: rawLength });
  } catch (error) {
    throw new Error(`PNG IDAT を解凍できません: ${error.message}`);
  }
  if (raw.length !== rawLength) throw new Error("PNG スキャンラインの長さが不正です");

  const data = Buffer.allocUnsafe(outputLength);
  for (let y = 0; y < height; y++) {
    const start = y * (rowLength + 1) + 1;
    const restore = FILTERS[raw[start - 1]];
    if (!restore) throw new Error("PNG フィルタが不正です");
    for (let x = 0; x < rowLength; x++) {
      const index = start + x;
      const left = x >= bytesPerPixel ? raw[index - bytesPerPixel] : 0;
      const up = y > 0 ? raw[index - rowLength - 1] : 0;
      const diagonal = y > 0 && x >= bytesPerPixel ? raw[index - rowLength - 1 - bytesPerPixel] : 0;
      raw[index] += restore(left, up, diagonal);
    }
    for (let x = 0; x < width; x++) {
      const index = start + x * bytesPerPixel;
      const target = (y * width + x) * 4;
      const first = raw[index];
      let red = first, green = first, blue = first, alpha = 255;
      if (colorType === 3) {
        const entry = first * 3;
        if (entry >= palette.length) throw new Error("PNG のパレット番号が範囲外です");
        [red, green, blue] = palette.subarray(entry, entry + 3);
        alpha = transparency?.[first] ?? 255;
      } else {
        if (colorType === 2 || colorType === 6) {
          green = raw[index + sampleBytes];
          blue = raw[index + sampleBytes * 2];
        }
        if (colorType === 4 || colorType === 6) alpha = raw[index + (channels - 1) * sampleBytes];
        if (transparency && (colorType === 0 || colorType === 2)) {
          let transparent = true;
          for (let channel = 0; channel < channels; channel++) {
            const offset = index + channel * sampleBytes;
            const sample = depth === 16 ? raw.readUInt16BE(offset) : raw[offset];
            transparent &&= sample === transparency.readUInt16BE(channel * 2);
          }
          if (transparent) alpha = 0;
        }
      }
      data[target] = red;
      data[target + 1] = green;
      data[target + 2] = blue;
      data[target + 3] = alpha;
    }
  }
  return { width, height, data };
}

function chunk(type, bytes) {
  const framed = Buffer.allocUnsafe(bytes.length + 12);
  framed.writeUInt32BE(bytes.length);
  framed.write(type, 4, 4, "ascii");
  bytes.copy(framed, 8);
  framed.writeUInt32BE(crc32(framed.subarray(4, -4)), framed.length - 4);
  return framed;
}

function encodePng({ width, height, data, colorType = 6 }) {
  if (colorType !== 0 && colorType !== 6) throw new Error("未対応の PNG 出力形式です");
  const channels = CHANNELS[colorType];
  if (imageLength(width, height, channels) !== data.length) throw new Error("PNG ピクセルデータの長さが不正です");
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = colorType;
  const stride = width * channels;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) data.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  return Buffer.concat([
    PNG_SIGNATURE, chunk("IHDR", header), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0)),
  ]);
}

module.exports = { PNG_SIGNATURE, imageLength, decodePng, encodePng };
