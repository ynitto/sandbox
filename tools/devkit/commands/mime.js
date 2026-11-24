"use strict";

const { statSync } = require("node:fs");
const { resolve } = require("node:path");
const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");
const { imageFormat } = require("../core/image-format");

const name = "mime";
const help = "ファイルの内容から MIME タイプを判定する";
const usage = "devkit mime [--json] <ファイル>...";
const example = "devkit mime photo.png";
const imageTypes = { PNG: "png", JPEG: "jpeg", GIF: "gif", WebP: "webp", BMP: "bmp", SVG: "svg+xml" };

const signatures = [
  [0, "255044462d", "application/pdf", "PDF"],
  [0, "504b0304", "application/zip", "ZIP"],
  [0, "504b0506", "application/zip", "ZIP"],
  [0, "504b0708", "application/zip", "ZIP"],
  [0, "1f8b", "application/gzip", "gzip"],
  [0, "377abcaf271c", "application/x-7z-compressed", "7z"],
  [0, "526172211a07", "application/vnd.rar", "RAR"],
  [257, "7573746172", "application/x-tar", "tar"],
  [0, "494433", "audio/mpeg", "MP3"],
  [4, "66747970", "video/mp4", "MP4"],
  [0, "7f454c46", "application/x-elf", "ELF"],
  [0, "4d5a", "application/vnd.microsoft.portable-executable", "PE"],
].map(([offset, hex, mime, description]) => [offset, Buffer.from(hex, "hex"), mime, description]);

function detect(buffer) {
  if (!buffer.length) return ["application/x-empty", "空ファイル"];
  const image = imageFormat(buffer);
  if (image) return ["image/" + imageTypes[image], image];
  for (const [offset, magic, mime, description] of signatures) {
    if (buffer.subarray(offset, offset + magic.length).equals(magic)) return [mime, description];
  }
  if (buffer.length >= 2 && buffer[0] === 0xff && (buffer[1] & 0xe0) === 0xe0 &&
      (buffer[1] & 0x18) !== 0x08 && (buffer[1] & 0x06) !== 0) return ["audio/mpeg", "MP3"];
  let decoded;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    // UTF-8 として読めない入力はバイナリとして扱う。
    return ["application/octet-stream", "不明なバイナリ"];
  }
  // JSON の構文解析は大きな入力で高コストになるため、サイズに上限を設ける。
  if (buffer.length <= 16 * 1024 * 1024) {
    try {
      JSON.parse(decoded);
      return ["application/json", "JSON"];
    } catch {
      // JSON として壊れている場合はテキストとして判定を続ける。
    }
  }
  const printable = decoded.replace(/[\t\r\n]|[^\p{C}]/gu, "");
  if (printable.length === 0 || (!decoded.includes("\0") && printable.length * 20 <= decoded.length)) {
    return ["text/plain", "UTF-8 テキスト"];
  }
  return ["application/octet-stream", "不明なバイナリ"];
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("files", { nargs: "+" });
  parser.addFlag("--json");
  const { files, json } = parser.parse(argv);
  const results = files.map((file) => {
    try {
      if (!statSync(resolve(io.cwd, file)).isFile()) throw new Error("通常のファイルを指定してください");
    } catch (error) {
      throw new UsageError(`${file}: ${error.message}`);
    }
    const [mime, description] = detect(readInput(undefined, file, io));
    return { file, mime, description };
  });
  if (json) io.out(JSON.stringify(results) + "\n");
  else for (const { file, mime } of results) io.out(`${file}: ${mime}\n`);
  return 0;
}

module.exports = { name, help, usage, example, run };
