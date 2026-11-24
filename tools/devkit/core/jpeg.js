"use strict";

function isJpeg(buffer) {
  return buffer[0] === 0xff && buffer[1] === 0xd8;
}

function* jpegSegments(buffer) {
  for (let offset = 2; offset < buffer.length;) {
    if (buffer[offset++] !== 0xff) throw new Error("JPEG マーカーが不正です");
    while (buffer[offset] === 0xff) offset++;
    const marker = buffer[offset++];
    if (marker === 0xd9 || marker === 0xda) return;
    if (marker === 1 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (marker === 0 || offset + 2 > buffer.length) throw new Error("JPEG セグメントが不正です");
    const length = buffer.readUInt16BE(offset);
    if (length < 2 || offset + length > buffer.length) throw new Error("JPEG セグメント長が不正です");
    yield { marker, data: buffer.subarray(offset + 2, offset + length) };
    offset += length;
  }
}

module.exports = { isJpeg, jpegSegments };
