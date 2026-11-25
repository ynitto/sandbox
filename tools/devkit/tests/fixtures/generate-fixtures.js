"use strict";

const { writeFileSync } = require("node:fs");
const { join } = require("node:path");
const { deflateSync } = require("node:zlib");

function chunk(type, data) {
  const payload = Buffer.concat([Buffer.from(type), data]);
  let crc = 0xffffffff;
  for (const byte of payload) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  const length = Buffer.alloc(4), checksum = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  checksum.writeUInt32BE((~crc) >>> 0);
  return Buffer.concat([length, payload, checksum]);
}

function png(width, height, colorType, scanlines, { palette, transparency, depth = 8, interlace = 0, idat } = {}) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = depth;
  header[9] = colorType;
  header[12] = interlace;
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    chunk("IHDR", header),
    ...(palette ? [chunk("PLTE", palette)] : []),
    ...(transparency ? [chunk("tRNS", transparency)] : []),
    chunk("IDAT", idat ?? deflateSync(Buffer.from(scanlines.flat()))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function frame(marker, bytes) {
  const header = Buffer.from([0xff, marker, 0, 0]);
  header.writeUInt16BE(bytes.length + 2, 2);
  return Buffer.concat([header, bytes]);
}

function exif({ littleEndian = true, latitudeRef = "N", longitudeRef = "E", original = true } = {}) {
  const tiff = Buffer.alloc(256);
  const u16 = (value, offset) => littleEndian ? tiff.writeUInt16LE(value, offset) : tiff.writeUInt16BE(value, offset);
  const u32 = (value, offset) => littleEndian ? tiff.writeUInt32LE(value, offset) : tiff.writeUInt32BE(value, offset);
  const main = 8, camera = 74, gps = 92;
  let nextValue = 146;
  tiff.write(littleEndian ? "II" : "MM");
  u16(42, 2);
  u32(main, 4);

  function directory(start, entries) {
    u16(entries.length, start);
    for (const [index, [tag, type, value]] of entries.entries()) {
      const offset = start + 2 + 12 * index;
      const count = type === 2 ? value.length + 1 : type === 5 ? value.length : 1;
      u16(tag, offset);
      u16(type, offset + 2);
      u32(count, offset + 4);
      let field = offset + 8;
      if (type === 5 || (type === 2 && count > 4)) {
        field = nextValue;
        u32(field, offset + 8);
        nextValue += type === 5 ? count * 8 : count;
      }
      if (type === 2) tiff.write(value + "\0", field, "ascii");
      if (type === 3) u16(value, field);
      if (type === 4) u32(value, field);
      if (type === 5) {
        for (const [i, [numerator, denominator]] of value.entries()) {
          u32(numerator, field + 8 * i);
          u32(denominator, field + 8 * i + 4);
        }
      }
    }
  }

  directory(main, [
    [0x010f, 2, "TestCam"], [0x0110, 2, "TC-1"], [0x0112, 3, 1],
    original ? [0x8769, 4, camera] : [0x0132, 2, "2026:11:24 10:00:00"], [0x8825, 4, gps],
  ]);
  if (original) directory(camera, [[0x9003, 2, "2026:11:24 10:00:00"]]);
  directory(gps, [
    [1, 2, latitudeRef], [2, 5, [[35, 1], [41, 1], [222, 10]]],
    [3, 2, longitudeRef], [4, 5, [[139, 1], [41, 1], [305, 10]]],
  ]);
  return Buffer.concat([Buffer.from("Exif\0\0"), tiff.subarray(0, nextValue)]);
}

function jpeg(metadata) {
  const sof = frame(0xc0, Buffer.from([8, 0, 16, 0, 32, 1, 1, 0x11, 0]));
  return Buffer.concat([
    Buffer.from([0xff, 0xd8]), ...(metadata ? [frame(0xe1, metadata)] : []), sof, Buffer.from([0xff, 0xd9]),
  ]);
}

function bmp() {
  const buffer = Buffer.alloc(70);
  buffer.write("BM");
  buffer.writeUInt32LE(buffer.length, 2);
  buffer.writeUInt32LE(54, 10);
  buffer.writeUInt32LE(40, 14);
  buffer.writeInt32LE(2, 18);
  buffer.writeInt32LE(2, 22);
  buffer.writeUInt16LE(1, 26);
  buffer.writeUInt16LE(24, 28);
  buffer.writeUInt32LE(16, 34);
  // Bottom row: blue, white. Top row: red, green. Each row has two padding bytes.
  buffer.set([255, 0, 0, 255, 255, 255, 0, 0, 0, 0, 255, 0, 255, 0, 0, 0], 54);
  return buffer;
}

function webp() {
  const buffer = Buffer.alloc(30);
  buffer.write("RIFF");
  buffer.writeUInt32LE(buffer.length - 8, 4);
  buffer.write("WEBPVP8X", 8);
  buffer.writeUInt32LE(10, 16);
  buffer.writeUIntLE(6, 24, 3);
  buffer.writeUIntLE(4, 27, 3);
  return buffer;
}

function generate() {
  const rgba = Array.from({ length: 4 }, (_, y) => [
    0, ...Array.from({ length: 4 }, (_, x) => [x * 60, y * 60, (x + y) * 30, 255 - y * 50 - x * 10]).flat(),
  ]);
  const fixtures = {
    "sample-rgba.png": png(4, 4, 6, rgba),
    "sample-gray.png": png(3, 2, 0, [[0, 0, 32, 64], [0, 128, 192, 255]]),
    "sample-palette.png": png(2, 2, 3, [[0, 0, 1], [0, 2, 3]], {
      palette: Buffer.from([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]),
      transparency: Buffer.from([255, 128, 64, 0]),
    }),
    "sample.jpg": jpeg(exif()),
    "sample-plain.jpg": jpeg(),
    "sample.gif": Buffer.concat([Buffer.from("GIF89a"), Buffer.from([2, 0, 3, 0, 0, 0, 0, 0x3b])]),
    "sample.bmp": bmp(),
    "sample.webp": webp(),
    "sample.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"></svg>\n',
  };
  for (const [file, bytes] of Object.entries(fixtures)) writeFileSync(join(__dirname, file), bytes);
}

if (require.main === module) generate();
module.exports = { png, jpeg, exif };
