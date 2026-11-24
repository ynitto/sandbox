"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");
const { isJpeg, jpegSegments } = require("../core/jpeg");

const name = "img-exif";
const help = "JPEG 画像の EXIF 撮影情報と GPS 座標を表示する";
const usage = "devkit img-exif [--json] <ファイル.jpg>";
const example = "devkit img-exif photo.jpg";

const ORIENTATION = [
  "", "通常", "左右反転", "180° 回転", "上下反転", "転置（左上・右下の対角線）",
  "90° 時計回り", "反転転置（右上・左下の対角線）", "90° 反時計回り",
];

function parseExif(tiff) {
  if (tiff.length < 8) throw new Error("TIFF ヘッダが不正です");
  const order = tiff.toString("latin1", 0, 2);
  if (order !== "II" && order !== "MM") throw new Error("TIFF のバイト順が不正です");
  const u16 = order === "II" ? (offset) => tiff.readUInt16LE(offset) : (offset) => tiff.readUInt16BE(offset);
  const u32 = order === "II" ? (offset) => tiff.readUInt32LE(offset) : (offset) => tiff.readUInt32BE(offset);
  if (u16(2) !== 42) throw new Error("TIFF ヘッダが不正です");

  function directory(offset) {
    const tags = new Map();
    if (!offset) return tags;
    const count = u16(offset);
    if (offset < 8 || offset + 2 + 12 * count + 4 > tiff.length) throw new Error("TIFF IFD が不正です");
    for (let index = 0; index < count; index++) {
      const entry = offset + 2 + 12 * index;
      const tag = u16(entry);
      tags.set(tag, { type: u16(entry + 2), count: u32(entry + 4), field: entry + 8 });
    }
    return tags;
  }

  function value(tags, tag, type, unit, count) {
    const entry = tags.get(tag);
    if (!entry || entry.type !== type || !entry.count || (count && count !== entry.count)) return undefined;
    const size = entry.count * unit;
    const offset = size <= 4 ? entry.field : u32(entry.field);
    // 4バイトを超える値は TIFF ヘッダ（先頭8バイト）の中を指してはいけない。
    if ((size > 4 && offset < 8) || offset + size > tiff.length) throw new Error("TIFF タグの値が範囲外です");
    return { offset, size };
  }

  function ascii(tags, tag) {
    const entry = value(tags, tag, 2, 1);
    return entry && tiff.toString("utf8", entry.offset, entry.offset + entry.size).split("\0")[0];
  }

  function subdirectory(tags, tag) {
    const entry = value(tags, tag, 4, 4, 1);
    return directory(entry && u32(entry.offset));
  }

  function coordinate(tags, tag, positive, negative) {
    const ref = ascii(tags, tag - 1);
    const entry = value(tags, tag, 5, 8, 3);
    if (!entry || (ref !== positive && ref !== negative)) return undefined;
    let result = 0;
    for (let i = 0; i < 3; i++) {
      const offset = entry.offset + 8 * i;
      const denominator = u32(offset + 4);
      if (!denominator) throw new Error("GPS 座標の分母が 0 です");
      result += u32(offset) / denominator / 60 ** i;
    }
    return Number((ref === negative ? -result : result).toFixed(6));
  }

  const main = directory(u32(4));
  const exif = subdirectory(main, 0x8769);
  const gps = subdirectory(main, 0x8825);
  const orientationEntry = value(main, 0x0112, 3, 2, 1);
  const orientation = orientationEntry && u16(orientationEntry.offset);
  const original = ascii(exif, 0x9003);
  const dateTime = ascii(main, 0x0132);
  return {
    make: ascii(main, 0x010f),
    model: ascii(main, 0x0110),
    orientation,
    orientationDescription: orientation === undefined ? undefined : ORIENTATION[orientation] || "不明",
    dateTimeOriginal: original,
    dateTime: original ? undefined : dateTime,
    latitude: coordinate(gps, 2, "N", "S"),
    longitude: coordinate(gps, 4, "E", "W"),
  };
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("file", { metavar: "ファイル.jpg" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { file, json } = parser.parse(argv);
  const input = readInput(undefined, file, io);
  if (!isJpeg(input)) throw new UsageError("入力は JPEG を指定してください");
  let result;
  try {
    for (const { marker, data } of jpegSegments(input)) {
      if (marker === 0xe1 && data.toString("latin1", 0, 6) === "Exif\0\0") {
        result = parseExif(data.subarray(6));
        break;
      }
    }
  } catch (error) {
    throw new UsageError(error.message);
  }
  if (!result) {
    io.err("EXIF がありません\n");
    return 1;
  }

  if (json) io.out(JSON.stringify(result) + "\n");
  else {
    const { make, model, orientation, orientationDescription, dateTimeOriginal, dateTime, latitude, longitude } = result;
    for (const [label, value] of [
      ["メーカー", make], ["機種", model],
      ["向き", orientation === undefined ? undefined : `${orientation} (${orientationDescription})`],
      ["撮影日時", dateTimeOriginal ?? dateTime],
      ["GPS 緯度", latitude?.toFixed(6)], ["GPS 経度", longitude?.toFixed(6)],
    ]) {
      if (value !== undefined) io.out(`${label}: ${value}\n`);
    }
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
