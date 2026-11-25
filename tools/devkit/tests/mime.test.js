"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const test = require("node:test");
const { runCli } = require("./helpers");

test("mime identifies the same image formats as img-info", () => {
  const cwd = join(__dirname, "fixtures");
  const files = ["sample-rgba.png", "sample.jpg", "sample.gif", "sample.webp", "sample.bmp", "sample.svg"];
  const result = runCli(["mime", "--json", ...files], { cwd });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).map(({ file, mime }) => [file, mime]), [
    ["sample-rgba.png", "image/png"], ["sample.jpg", "image/jpeg"], ["sample.gif", "image/gif"],
    ["sample.webp", "image/webp"], ["sample.bmp", "image/bmp"], ["sample.svg", "image/svg+xml"],
  ]);
  assert.match(runCli(["mime", "sample.svg"], { cwd }).stdout, /^sample\.svg: image\/svg\+xml\n$/);
});

test("mime recognizes signatures, text, empty files and binary fallback", (t) => {
  const cwd = fs.mkdtempSync(join(tmpdir(), "devkit-mime-"));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const samples = [
    ["pdf", "%PDF-", "application/pdf"], ["zip", "PK\x03\x04", "application/zip"],
    ["gz", Buffer.from("1f8b", "hex"), "application/gzip"], ["7z", Buffer.from("377abcaf271c", "hex"), "application/x-7z-compressed"],
    ["rar", "Rar!\x1a\x07", "application/vnd.rar"], ["tar", "\0".repeat(257) + "ustar", "application/x-tar"],
    ["mp3", "ID3", "audio/mpeg"], ["frame", Buffer.from("fffb", "hex"), "audio/mpeg"],
    ["mp4", "\0\0\0\x18ftyp", "video/mp4"], ["elf", "\x7fELF", "application/x-elf"],
    ["pe", "MZ", "application/vnd.microsoft.portable-executable"],
    ["empty", "", "application/x-empty"], ["json", '{"a":"<svg/>"}', "application/json"],
    ["text", "こんにちは\n", "text/plain"], ["raw", "\0\x01\x02", "application/octet-stream"],
  ];
  for (const [file, data] of samples) fs.writeFileSync(join(cwd, file), data);
  const result = runCli(["mime", "--json", ...samples.map(([file]) => file)], { cwd });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout).map(({ mime }) => mime), samples.map(([, , mime]) => mime));
  for (const file of [".", "missing"]) assert.equal(runCli(["mime", file], { cwd }).status, 2);
});
