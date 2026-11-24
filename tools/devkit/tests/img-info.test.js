"use strict";

const assert = require("node:assert/strict");
const { statSync, mkdtempSync, writeFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");

const cwd = join(__dirname, "fixtures");

test("img-info identifies all fixture formats and reports dimensions and sizes", () => {
  const fixtures = [
    ["sample-rgba.png", "PNG", 4, 4, "1:1"],
    ["sample-gray.png", "PNG", 3, 2, "3:2"],
    ["sample-palette.png", "PNG", 2, 2, "1:1"],
    ["sample.jpg", "JPEG", 32, 16, "2:1"],
    ["sample-plain.jpg", "JPEG", 32, 16, "2:1"],
    ["sample.gif", "GIF", 2, 3, "2:3"],
    ["sample.bmp", "BMP", 2, 2, "1:1"],
    ["sample.webp", "WebP", 7, 5, "7:5"],
    ["sample.svg", "SVG", 120, 80, "3:2"],
  ];
  const result = runCli(["img-info", "--json", ...fixtures.map(([file]) => file)], { cwd });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), fixtures.map(([file, format, width, height, ratio]) => ({
    file, format, width, height, bytes: statSync(join(cwd, file)).size, aspect: { ratio, decimal: width / height },
  })));
  assert.match(runCli(["img-info", "sample.gif"], { cwd }).stdout, /2:3 \(0\.666667\)/);
});

test("img-info distinguishes unknown, dimensionless and missing files", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-info-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "unknown.png"), "hello");
  writeFileSync(join(dir, "empty.svg"), "<svg/>");
  writeFileSync(join(dir, "viewbox"), "<svg viewBox='-2,0,16,9'/>");
  const result = runCli(["img-info", "--json", "unknown.png", "empty.svg", "viewbox"], { cwd: dir });
  assert.equal(result.status, 1);
  const [unknown, empty, svg] = JSON.parse(result.stdout);
  assert.deepEqual(unknown, { file: "unknown.png", format: null, width: null, height: null, bytes: 5, aspect: null });
  assert.equal(empty.format, "SVG");
  assert.equal(empty.aspect, null);
  assert.deepEqual([svg.width, svg.height], [16, 9]);
  assert.match(runCli(["img-info", "unknown.png"], { cwd: dir }).stdout, /形式: 不明/);
  assert.equal(runCli(["img-info", "missing.png"], { cwd: dir }).status, 2);
});

test("img-info reads VP8 and VP8L dimensions across RIFF chunks", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-webp-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const lossy = Buffer.from([0, 0, 0, 0x9d, 0x01, 0x2a, 0x20, 0x80, 0x10, 0xc0]);
  const lossless = Buffer.alloc(5);
  lossless[0] = 0x2f;
  lossless.writeUInt32LE(31 | (15 << 14) | (1 << 28), 1);
  for (const [file, type, data] of [["lossy.webp", "VP8 ", lossy], ["lossless.webp", "VP8L", lossless]]) {
    const header = Buffer.alloc(30);
    header.write("RIFF");
    header.writeUInt32LE(header.length + data.length + data.length % 2 - 8, 4);
    header.write("WEBPJUNK", 8);
    header.writeUInt32LE(1, 16);
    header.write(type, 22);
    header.writeUInt32LE(data.length, 26);
    writeFileSync(join(dir, file), Buffer.concat([header, data, Buffer.alloc(data.length % 2)]));
  }
  const result = runCli(["img-info", "--json", "lossy.webp", "lossless.webp"], { cwd: dir });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).map(({ width, height }) => [width, height]), [[32, 16], [32, 16]]);
});
