"use strict";

const assert = require("node:assert/strict");
const { mkdtempSync, readFileSync, writeFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCliBuffer } = require("./helpers");
const { readPng } = require("./fixtures/read-png");

const cwd = join(__dirname, "fixtures");
function convert(...args) {
  const result = runCliBuffer(["img-convert", ...args], { cwd });
  assert.equal(result.status, 0, result.stderr.toString());
  return result.stdout;
}

test("img-convert round-trips RGBA via 32-bit BMP, including alpha", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-convert-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const bmpFile = join(dir, "roundtrip.BMP");
  assert.equal(convert("sample-rgba.png", "--out", bmpFile).length, 0);
  const bmp = readFileSync(bmpFile);
  assert.equal(bmp.readUInt16LE(28), 32);
  assert.deepEqual([...bmp.subarray(54, 58)], [90, 180, 0, 105]);
  const expected = Array.from({ length: 4 }, (_, y) =>
    Array.from({ length: 4 }, (_, x) => [x * 60, y * 60, (x + y) * 30, 255 - 50 * y - 10 * x]).flat());
  assert.deepEqual(readPng(convert(bmpFile, "--to", "png")).pixels, expected);
});

test("img-convert decodes 24-bit BMP with alpha 255 in both row orders", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-bmp-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const bottomUp = readFileSync(join(cwd, "sample.bmp"));
  const topDown = Buffer.concat([bottomUp.subarray(0, 54), bottomUp.subarray(62), bottomUp.subarray(54, 62)]);
  topDown.writeInt32LE(-2, 22);
  const file = join(dir, "topdown.bmp");
  writeFileSync(file, topDown);
  const pixels = [[255, 0, 0, 255, 0, 255, 0, 255], [0, 0, 255, 255, 255, 255, 255, 255]];
  for (const input of ["sample.bmp", file]) assert.deepEqual(readPng(convert(input, "--to", "png")).pixels, pixels);
});

test("img-convert applies BT.601 and gives --to precedence over extension", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-gray-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "gray.bmp");
  convert("sample.bmp", "--out", file, "--to", "png", "--grayscale");
  assert.deepEqual(readPng(readFileSync(file)), {
    width: 2, height: 2, colorType: 0, pixels: [[76, 150], [29, 255]],
  });
  const bmp = convert("sample-palette.png", "--to", "bmp", "--grayscale");
  assert.deepEqual([...bmp.subarray(54)], [29, 29, 29, 64, 255, 255, 255, 0, 76, 76, 76, 255, 150, 150, 150, 128]);
});

test("img-convert requires a supported input and output type", () => {
  for (const options of [["sample.bmp"], ["sample.bmp", "--out", "file.jpg"], ["sample.jpg", "--to", "png"]]) {
    assert.equal(runCliBuffer(["img-convert", ...options], { cwd }).status, 2);
  }
});
