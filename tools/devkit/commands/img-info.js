"use strict";

const { ArgParser } = require("../core/args");
const { readInput } = require("../core/command-input");
const { imageFormat, svgTag } = require("../core/image-format");
const { jpegSegments } = require("../core/jpeg");

const name = "img-info";
const help = "画像の形式・寸法・ファイルサイズ・アスペクト比を表示する";
const usage = "devkit img-info [--json] <ファイル>...";
const example = "devkit img-info photo.jpg";

function jpegSize(buffer) {
  try {
    for (const { marker, data } of jpegSegments(buffer)) {
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker) && data.length >= 6) {
        return [data.readUInt16BE(3), data.readUInt16BE(1)];
      }
    }
  } catch {
    // 判定済みの形式は、寸法を読み取れなくても表示する。
  }
  return [];
}

function webpSize(buffer) {
  const end = Math.min(buffer.length, buffer.readUInt32LE(4) + 8);
  for (let offset = 12; offset + 8 <= end;) {
    const type = buffer.toString("latin1", offset, offset + 4);
    const length = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    const available = Math.min(length, end - start);
    if (type === "VP8X" && available >= 10) {
      return [buffer.readUIntLE(start + 4, 3) + 1, buffer.readUIntLE(start + 7, 3) + 1];
    }
    if (type === "VP8L" && available >= 5 && buffer[start] === 0x2f) {
      const bits = buffer.readUInt32LE(start + 1);
      return [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1];
    }
    if (type === "VP8 " && available >= 10 && !(buffer[start] & 1) &&
        buffer.readUIntBE(start + 3, 3) === 0x9d012a) {
      return [buffer.readUInt16LE(start + 6) & 0x3fff, buffer.readUInt16LE(start + 8) & 0x3fff];
    }
    offset = start + length + (length % 2);
  }
  return [];
}

function svgSize(attributes) {
  const values = new Map();
  for (const match of attributes.matchAll(/\s+([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
    values.set(match[1], match[2] ?? match[3]);
  }
  function pixels(key) {
    const value = values.get(key)?.trim();
    if (!value) return undefined;
    const match = /^([+-]?(?:\d*\.\d+|\d+\.?\d*)(?:e[+-]?\d+)?)(px|in|cm|mm|q|pt|pc)?$/i.exec(value);
    if (!match) return undefined;
    const units = { px: 1, in: 96, cm: 96 / 2.54, mm: 96 / 25.4, q: 96 / 101.6, pt: 96 / 72, pc: 16 };
    const size = Number(match[1]) * units[match[2]?.toLowerCase() ?? "px"];
    return Number.isFinite(size) && size > 0 ? size : undefined;
  }
  const width = pixels("width");
  const height = pixels("height");
  if (width && height) return [width, height];
  const viewBox = values.get("viewBox")?.trim().split(/[\s,]+/).map(Number);
  if (viewBox?.length === 4 && viewBox.every(Number.isFinite) && viewBox[2] > 0 && viewBox[3] > 0) {
    if (width) return [width, width / viewBox[2] * viewBox[3]];
    if (height) return [height / viewBox[3] * viewBox[2], height];
    return viewBox.slice(2);
  }
  return [width, height];
}

function inspect(buffer) {
  switch (imageFormat(buffer)) {
    case "PNG":
      return buffer.length >= 24 && buffer.toString("latin1", 12, 16) === "IHDR"
        ? ["PNG", buffer.readUInt32BE(16), buffer.readUInt32BE(20)] : ["PNG"];
    case "JPEG": return ["JPEG", ...jpegSize(buffer)];
    case "GIF":
      return buffer.length >= 10 ? ["GIF", buffer.readUInt16LE(6), buffer.readUInt16LE(8)] : ["GIF"];
    case "WebP": return ["WebP", ...webpSize(buffer)];
    case "BMP":
      if (buffer.length >= 26 && buffer.readUInt32LE(14) >= 40) {
        return ["BMP", buffer.readInt32LE(18), Math.abs(buffer.readInt32LE(22))];
      }
      if (buffer.length >= 22 && buffer.readUInt32LE(14) === 12) {
        return ["BMP", buffer.readUInt16LE(18), buffer.readUInt16LE(20)];
      }
      return ["BMP"];
    case "SVG": return ["SVG", ...svgSize(svgTag(buffer)[1])];
    default: return [];
  }
}

function aspectRatio(width, height) {
  // Convert decimal dimensions to integers before reducing the ratio.
  function decimalParts(value) {
    const [mantissa, exponent = "0"] = value.toString().split("e");
    const [whole, fraction = ""] = mantissa.split(".");
    return [BigInt(whole + fraction), Number(exponent) - fraction.length];
  }
  const [w, wPower] = decimalParts(width);
  const [h, hPower] = decimalParts(height);
  const power = Math.min(wPower, hPower);
  const numerator = w * 10n ** BigInt(wPower - power);
  const denominator = h * 10n ** BigInt(hPower - power);
  let a = numerator, b = denominator;
  while (b) [a, b] = [b, a % b];
  return { ratio: `${numerator / a}:${denominator / a}`, decimal: width / height };
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("files", { metavar: "ファイル", nargs: "+" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { files, json } = parser.parse(argv);
  const dimension = (value) => Number.isFinite(value) && value > 0 ? value : null;
  const results = files.map((file) => {
    const buffer = readInput(undefined, file, io);
    const [format = null, w, h] = inspect(buffer);
    const width = dimension(w), height = dimension(h);
    return { file, format, width, height, bytes: buffer.length, aspect: width && height ? aspectRatio(width, height) : null };
  });
  if (json) io.out(JSON.stringify(results) + "\n");
  else for (const { file, format, width, height, bytes, aspect } of results) {
    io.out(`${file}\n形式: ${format ?? "不明"}\n幅: ${width ?? "不明"}\n高さ: ${height ?? "不明"}\n` +
      `ファイルサイズ: ${bytes} バイト\nアスペクト比: ${aspect ? `${aspect.ratio} (${aspect.decimal.toFixed(6)})` : "不明"}\n`);
  }
  return results.some(({ format }) => format === null) ? 1 : 0;
}

module.exports = { name, help, usage, example, run };
