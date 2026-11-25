"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput, integer } = require("../core/command-input");
const { writeOutput } = require("../core/command-output");
const { decodePng, encodePng, imageLength } = require("../core/png");

const name = "img-resize";
const help = "PNG 画像を指定サイズにリサイズする";
const usage = "devkit img-resize <入力.png> [--out 出力.png] [--width N] [--height N] [--scale F] [--keep-aspect] [--method nearest|bilinear]";
const example = "devkit img-resize photo.png --width 800 --out photo-small.png";

function resize(image, width, height, method) {
  const data = Buffer.allocUnsafe(imageLength(width, height, 4));
  const xRatio = image.width / width;
  const yRatio = image.height / height;
  const clamp = (position, size) => Math.min(size - 1, Math.max(0, position));
  for (let y = 0; y < height; y++) {
    const sourceY = clamp((y + 0.5) * yRatio - 0.5, image.height);
    for (let x = 0; x < width; x++) {
      const sourceX = clamp((x + 0.5) * xRatio - 0.5, image.width);
      const target = (y * width + x) * 4;
      if (method === "nearest") {
        const index = (Math.round(sourceY) * image.width + Math.round(sourceX)) * 4;
        image.data.copy(data, target, index, index + 4);
        continue;
      }
      const x0 = Math.floor(sourceX), x1 = clamp(x0 + 1, image.width);
      const y0 = Math.floor(sourceY), y1 = clamp(y0 + 1, image.height);
      const dx = sourceX - x0, dy = sourceY - y0;
      for (let channel = 0; channel < 4; channel++) {
        const top = image.data[(y0 * image.width + x0) * 4 + channel] * (1 - dx) +
          image.data[(y0 * image.width + x1) * 4 + channel] * dx;
        const bottom = image.data[(y1 * image.width + x0) * 4 + channel] * (1 - dx) +
          image.data[(y1 * image.width + x1) * 4 + channel] * dx;
        data[target + channel] = Math.round(top * (1 - dy) + bottom * dy);
      }
    }
  }
  return { width, height, data };
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("file", { metavar: "入力.png" });
  parser.addOption("--out", { help: "出力先（省略時は標準出力）" });
  parser.addOption("--width", { metavar: "N", type: integer(1), help: "出力幅" });
  parser.addOption("--height", { metavar: "N", type: integer(1), help: "出力高さ" });
  parser.addOption("--scale", { metavar: "F", help: "拡大・縮小倍率" });
  parser.addFlag("--keep-aspect", { help: "指定した枠内で縦横比を維持する" });
  parser.addOption("--method", { choices: ["nearest", "bilinear"], def: "bilinear", help: "補間方式（既定値: bilinear）" });
  const { file, out, width, height, scale, keep_aspect, method } = parser.parse(argv);
  if (scale !== undefined && (width || height)) throw new UsageError("--scale と --width/--height は併用できません");
  if (scale === undefined && !width && !height) throw new UsageError("--width、--height、--scale のいずれかを指定してください");
  const factor = Number(scale);
  if (scale !== undefined && (!Number.isFinite(factor) || factor <= 0)) throw new UsageError("--scale は 0 より大きい数を指定してください");

  const input = readInput(undefined, file, io);
  let output;
  try {
    const image = decodePng(input);
    const ratio = scale !== undefined ? factor : Math.min(
      width === undefined ? Infinity : width / image.width,
      height === undefined ? Infinity : height / image.height,
    );
    const scaled = (dimension) => Math.max(1, Math.round(dimension * ratio));
    const targetWidth = keep_aspect || !width ? scaled(image.width) : width;
    const targetHeight = keep_aspect || !height ? scaled(image.height) : height;
    output = encodePng(resize(image, targetWidth, targetHeight, method));
  } catch (error) {
    throw new UsageError(error.message);
  }
  writeOutput(output, out, io);
  return 0;
}

module.exports = { name, help, usage, example, run };
