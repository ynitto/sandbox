"use strict";

const assert = require("node:assert/strict");
const { mkdtempSync, readFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");
const { encodeQr } = require("../core/qr");

const vector = "HELLO WORLD";

test("QR v1-M matches the Thonky HELLO WORLD data and Reed–Solomon vector", () => {
  const qr = encodeQr(vector, "M");
  assert.equal(qr.version, 1);
  assert.equal(qr.size, 21);
  assert.deepEqual(qr.dataCodewords, [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]);
  assert.deepEqual(qr.errorCorrectionCodewords, [196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
});

const FORMAT_M = [0x5412, 0x5125, 0x5e7c, 0x5b4b, 0x45f9, 0x40ce, 0x4f97, 0x4aa0];
const maskApplies = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (_r, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => ((r >> 1) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => r * c % 2 + r * c % 3 === 0,
  (r, c) => (r * c % 2 + r * c % 3) % 2 === 0,
  (r, c) => ((r + c) % 2 + r * c % 3) % 2 === 0,
];

function extractCodewords({ size, modules, version }, mask) {
  const centers = version === 1 ? [] : version === 10 ? [6, 28, 50] : [6, 30];
  const alignments = centers.flatMap((r) => centers.map((c) => [r, c]))
    .filter(([r, c]) => !((r === 6 && (c === 6 || c === size - 7)) || (r === size - 7 && c === 6)));
  const reserved = (r, c) =>
    (r <= 8 && (c <= 8 || c >= size - 8)) || (r >= size - 8 && c <= 8) ||
    r === 6 || c === 6 ||
    (version >= 7 && ((r < 6 && c >= size - 11) || (c < 6 && r >= size - 11))) ||
    alignments.some(([y, x]) => Math.abs(y - r) <= 2 && Math.abs(x - c) <= 2);
  const columns = Array.from({ length: size }, (_, i) => size - i - 1).filter((c) => c !== 6);
  const bits = [];
  for (let index = 0; index < columns.length; index += 2) {
    for (let step = 0; step < size; step++) {
      const row = index % 4 === 0 ? size - 1 - step : step;
      for (const column of columns.slice(index, index + 2)) {
        if (!reserved(row, column)) bits.push(Number(modules[row][column] !== maskApplies[mask](row, column)));
      }
    }
  }
  const words = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) words.push(parseInt(bits.slice(i, i + 8).join(""), 2));
  assert.ok(bits.slice(words.length * 8).every((bit) => bit === 0));
  return words;
}

test("QR final matrices carry their mask and the complete placed codeword stream", () => {
  for (const [text, version] of [[vector, 1], ["a".repeat(70), 5], ["a".repeat(190), 10]]) {
    const qr = encodeQr(text, "M");
    const { size, modules } = qr;
    assert.equal(qr.version, version);
    for (const row of modules) {
      assert.equal(row.length, size);
      assert.ok(row.every((module) => typeof module === "boolean"));
    }
    const first = [[0, 8], [1, 8], [2, 8], [3, 8], [4, 8], [5, 8], [7, 8], [8, 8],
      [8, 7], [8, 5], [8, 4], [8, 3], [8, 2], [8, 1], [8, 0]];
    const second = Array.from({ length: 15 }, (_, i) => i < 8 ? [8, size - 1 - i] : [size - 15 + i, 8]);
    const readBits = (coords) => coords.reduce((word, [y, x], i) => word | (Number(modules[y][x]) << i), 0);
    const format = readBits(first);
    assert.equal(readBits(second), format);
    const mask = FORMAT_M.indexOf(format);
    assert.notEqual(mask, -1);
    assert.equal(mask, qr.mask);
    const expected = version === 1 ? [...qr.dataCodewords, ...qr.errorCorrectionCodewords] : qr.interleavedCodewords;
    assert.deepEqual(extractCodewords(qr, mask), expected);
  }
});

test("QR v10-M interleaves short and long data blocks according to their lengths", () => {
  const qr = encodeQr("a".repeat(190), "M");
  const data = qr.dataCodewords;
  // 10-M uses four 43-byte blocks and one 44-byte block.
  const starts = [0, 43, 86, 129, 172];
  const expected = Array.from({ length: 43 }, (_, i) => starts.map((start) => data[start + i])).flat();
  assert.deepEqual(qr.interleavedCodewords.slice(0, 216), [...expected, data[215]]);
});

test("QR chooses numeric or UTF-8 byte mode across error correction levels", () => {
  for (const level of ["L", "M", "Q", "H"]) {
    const numeric = encodeQr("01234567", level);
    assert.equal(numeric.dataCodewords[0] >> 4, 1);
    const unicode = encodeQr("日", level);
    assert.deepEqual(unicode.dataCodewords.slice(0, 6), [0x71, 0xa4, 3, 0xe6, 0x97, 0xa5]);
  }
  assert.equal(encodeQr("1".repeat(652), "L").version, 10);
  assert.throws(() => encodeQr("1".repeat(653), "L"), /容量/);
});

test("qr renders precisely the dark modules in an SVG with a quiet zone", () => {
  const result = runCli(["qr", vector, "--border", "2"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /<svg[^>]+viewBox="0 0 25 25"/);
  assert.match(result.stdout, /<rect[^>]+fill="white"/);
  const darkCoordinates = [...result.stdout.matchAll(/M(\d+) (\d+)h1v1h-1z/g)]
    .map(([, x, y]) => [Number(x) - 2, Number(y) - 2]);
  const { modules } = encodeQr(vector);
  const expected = modules.flatMap((row, y) => row.flatMap((dark, x) => dark ? [[x, y]] : []));
  assert.deepEqual(darkCoordinates, expected);
});

test("qr writes stdin text to --out with default border and rejects invalid options", (t) => {
  const cwd = mkdtempSync(join(tmpdir(), "devkit-qr-"));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const result = runCli(["qr", "--out", "hello.svg"], { cwd, input: vector });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "");
  assert.match(readFileSync(join(cwd, "hello.svg"), "utf8"), /viewBox="0 0 29 29"/);
  assert.equal(runCli(["qr", "--level", "Z", vector]).status, 2);
  assert.equal(runCli(["qr", "--border=-1", vector]).status, 2);
});
