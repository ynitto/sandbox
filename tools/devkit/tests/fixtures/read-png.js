"use strict";

const assert = require("node:assert/strict");
const { inflateSync } = require("node:zlib");

function readPng(buffer) {
  assert.equal(buffer.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  const colorType = buffer[25];
  assert.ok([0, 6].includes(colorType));
  assert.equal(buffer[24], 8);
  const chunks = [];
  for (let offset = 8; offset + 12 <= buffer.length;) {
    const length = buffer.readUInt32BE(offset);
    if (buffer.toString("ascii", offset + 4, offset + 8) === "IDAT") {
      chunks.push(buffer.subarray(offset + 8, offset + 8 + length));
    }
    offset += length + 12;
  }
  const raw = inflateSync(Buffer.concat(chunks));
  const stride = width * (colorType === 6 ? 4 : 1) + 1;
  assert.equal(raw.length, height * stride);
  const pixels = [];
  for (let y = 0; y < height; y++) {
    assert.equal(raw[y * stride], 0);
    pixels.push([...raw.subarray(y * stride + 1, (y + 1) * stride)]);
  }
  return { width, height, colorType, pixels };
}

module.exports = { readPng };
