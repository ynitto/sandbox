"use strict";

const assert = require("node:assert/strict");
const { mkdtempSync, writeFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");
const { jpeg, exif } = require("./fixtures/generate-fixtures");

const cwd = join(__dirname, "fixtures");

test("img-exif reads camera, orientation, timestamp and GPS", () => {
  const result = runCli(["img-exif", "--json", "sample.jpg"], { cwd });
  assert.equal(result.status, 0, result.stderr);
  const { make, model, orientation, orientationDescription, dateTimeOriginal, latitude, longitude } = JSON.parse(result.stdout);
  assert.deepEqual({ make, model, orientation, orientationDescription, dateTimeOriginal }, {
    make: "TestCam", model: "TC-1", orientation: 1, orientationDescription: "通常", dateTimeOriginal: "2026:11:24 10:00:00",
  });
  assert.ok(Math.abs(latitude - 35.689500) <= 0.000001);
  assert.ok(Math.abs(longitude - 139.691806) <= 0.000001);
  assert.match(runCli(["img-exif", "sample.jpg"], { cwd }).stdout, /35\.689500\nGPS 経度: 139\.691806/);
});

test("img-exif reads big-endian TIFF with south/west coordinates and DateTime fallback", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "devkit-exif-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "big.jpg"), jpeg(exif({ littleEndian: false, latitudeRef: "S", longitudeRef: "W", original: false })));
  const result = runCli(["img-exif", "--json", "big.jpg"], { cwd: dir });
  assert.equal(result.status, 0, result.stderr);
  const { orientation, dateTimeOriginal, dateTime, latitude, longitude } = JSON.parse(result.stdout);
  assert.deepEqual({ orientation, dateTimeOriginal, dateTime, latitude, longitude }, {
    orientation: 1, dateTimeOriginal: undefined, dateTime: "2026:11:24 10:00:00", latitude: -35.6895, longitude: -139.691806,
  });
});

test("img-exif reports no EXIF separately from non-JPEG input", () => {
  const plain = runCli(["img-exif", "sample-plain.jpg"], { cwd });
  assert.equal(plain.status, 1);
  assert.match(plain.stderr, /EXIF がありません/);
  assert.equal(plain.stdout, "");
  assert.equal(runCli(["img-exif", "sample.gif"], { cwd }).status, 2);
});
