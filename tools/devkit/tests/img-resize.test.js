"use strict";

const assert = require("node:assert/strict");
const { mkdtempSync, readFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCliBuffer } = require("./helpers");
const { readPng } = require("./fixtures/read-png");
const { png } = require("./fixtures/generate-fixtures");
const { decodePng } = require("../core/png");

const cwd = join(__dirname, "fixtures");

function resize(file, ...options) {
  const result = runCliBuffer(["img-resize", file, ...options], { cwd });
  assert.equal(result.status, 0, result.stderr.toString());
  return readPng(result.stdout);
}

test("img-resize nearest enlarges 2×2 palette PNG with transparency", () => {
  const red = [255, 0, 0, 255], green = [0, 255, 0, 128];
  const blue = [0, 0, 255, 64], white = [255, 255, 255, 0];
  const top = [...red, ...red, ...green, ...green];
  const bottom = [...blue, ...blue, ...white, ...white];
  assert.deepEqual(resize("sample-palette.png", "--scale", "2", "--method", "nearest"), {
    width: 4, height: 4, colorType: 6, pixels: [top, top, bottom, bottom],
  });
});

test("img-resize nearest shrinks 4×4 RGBA and defaults to centered bilinear", () => {
  assert.deepEqual(resize("sample-rgba.png", "--width", "2", "--method", "nearest"), {
    width: 2, height: 2, colorType: 6,
    pixels: [[60, 60, 60, 195, 180, 60, 120, 175], [60, 180, 120, 95, 180, 180, 180, 75]],
  });
  // At (0, 0), the four source pixels have equal weight, including their alpha values.
  assert.deepEqual(resize("sample-rgba.png", "--height", "2").pixels[0].slice(0, 4), [30, 30, 30, 225]);
  assert.deepEqual(resize("sample-palette.png", "--width", "4").pixels[0].slice(0, 8), [255, 0, 0, 255, 191, 64, 0, 223]);
});

test("img-resize resolves aspect-fitting, exact, and minimum dimensions", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-resize-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const out = join(dir, "resized.png");
  const result = runCliBuffer(["img-resize", "sample-gray.png", "--width", "9", "--height", "5",
    "--keep-aspect", "--out", out], { cwd });
  assert.equal(result.status, 0, result.stderr.toString());
  assert.equal(result.stdout.length, 0);
  const { width, height } = readPng(readFileSync(out));
  assert.deepEqual([width, height], [8, 5]);
  assert.equal(resize("sample-gray.png", "--width", "9", "--height", "5").width, 9);
  assert.deepEqual(resize("sample-gray.png", "--scale", "0.01").pixels[0].length, 4);
});

test("img-resize rejects invalid options and unsupported input", () => {
  for (const args of [
    ["sample-rgba.png"], ["sample-rgba.png", "--scale", "0"], ["sample-rgba.png", "--width", "0"],
    ["sample-rgba.png", "--scale", "1", "--height", "2"], ["sample.bmp", "--width", "2"],
  ]) assert.equal(runCliBuffer(["img-resize", ...args], { cwd }).status, 2, args.join(" "));
});

test("PNG restores all five filters and takes the high byte of 16-bit samples", () => {
  const source = png(2, 5, 4, [
    [0, 10, 20, 30, 40],
    [1, 12, 22, 20, 20],
    [2, 2, 2, 2, 2],
    [3, 9, 14, 12, 12],
    [4, 2, 2, 2, 2],
  ]);
  const data = decodePng(source).data;
  const expected = [10, 20, 30, 40, 12, 22, 32, 42, 14, 24, 34, 44, 16, 26, 37, 47, 18, 28, 39, 49];
  assert.deepEqual([...data], expected.flatMap((value, index) => index % 2 ? [value] : [value, value, value]));
  assert.deepEqual([...decodePng(png(1, 1, 2, [[0, 12, 255, 34, 123, 56, 99]], { depth: 16 })).data],
    [12, 34, 56, 255]);
});

test("PNG rejects invalid signature, CRC, IDAT and Adam7", () => {
  const damaged = readFileSync(join(cwd, "sample-rgba.png"));
  damaged[16] ^= 1;
  assert.throws(() => decodePng(Buffer.alloc(8)), /シグネチャ/);
  assert.throws(() => decodePng(damaged), /CRC/);
  assert.throws(() => decodePng(png(1, 1, 0, [], { idat: Buffer.from("bad") })), /IDAT/);
  assert.throws(() => decodePng(png(1, 1, 0, [[0, 0]], { interlace: 1 })), /Adam7/);
});
