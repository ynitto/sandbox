"use strict";

const { imageLength } = require("./png");

function decodeBmp(buffer) {
  if (buffer.length < 54 || buffer.toString("latin1", 0, 2) !== "BM") {
    throw new Error("BMP ヘッダが不正です");
  }
  const pixelOffset = buffer.readUInt32LE(10);
  const headerSize = buffer.readUInt32LE(14);
  const width = buffer.readInt32LE(18);
  const signedHeight = buffer.readInt32LE(22);
  const height = Math.abs(signedHeight);
  const planes = buffer.readUInt16LE(26);
  const depth = buffer.readUInt16LE(28);
  if (headerSize < 40 || planes !== 1 || ![24, 32].includes(depth) || buffer.readUInt32LE(30) !== 0) {
    throw new Error("BMP は BI_RGB の 24bit / 32bit に対応しています");
  }
  const length = imageLength(width, height, 4);
  const pixelBytes = depth / 8;
  const stride = Math.ceil(width * pixelBytes / 4) * 4;
  if (pixelOffset < 14 + headerSize || pixelOffset + stride * height > buffer.length) {
    throw new Error("BMP ピクセルデータが不正です");
  }
  const data = Buffer.allocUnsafe(length);
  for (let y = 0; y < height; y++) {
    const row = signedHeight < 0 ? y : height - 1 - y;
    for (let x = 0; x < width; x++) {
      const source = pixelOffset + row * stride + x * pixelBytes;
      const target = (y * width + x) * 4;
      data[target] = buffer[source + 2];
      data[target + 1] = buffer[source + 1];
      data[target + 2] = buffer[source];
      data[target + 3] = depth === 32 ? buffer[source + 3] : 255;
    }
  }
  return { width, height, data };
}

function encodeBmp({ width, height, data }) {
  const length = imageLength(width, height, 4);
  if (length !== data.length || length + 54 > 0xffffffff) throw new Error("BMP ピクセルデータの長さが不正です");
  const output = Buffer.alloc(length + 54);
  output.write("BM");
  output.writeUInt32LE(output.length, 2);
  output.writeUInt32LE(54, 10);
  output.writeUInt32LE(40, 14);
  output.writeInt32LE(width, 18);
  output.writeInt32LE(height, 22);
  output.writeUInt16LE(1, 26);
  output.writeUInt16LE(32, 28);
  output.writeUInt32LE(length, 34);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const source = (y * width + x) * 4;
      const target = 54 + ((height - 1 - y) * width + x) * 4;
      output[target] = data[source + 2];
      output[target + 1] = data[source + 1];
      output[target + 2] = data[source];
      output[target + 3] = data[source + 3];
    }
  }
  return output;
}

module.exports = { decodeBmp, encodeBmp };
