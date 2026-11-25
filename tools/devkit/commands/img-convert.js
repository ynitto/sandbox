"use strict";

const { extname } = require("node:path");
const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");
const { writeOutput } = require("../core/command-output");
const { PNG_SIGNATURE, decodePng, encodePng } = require("../core/png");
const { decodeBmp, encodeBmp } = require("../core/bmp");

const name = "img-convert";
const help = "PNG と BMP を変換し、必要に応じてグレースケール化する";
const usage = "devkit img-convert <入力> [--out 出力] [--to png|bmp] [--grayscale]";
const example = "devkit img-convert photo.png --out photo.bmp";

function grayscale(image, to) {
  const { data, width, height } = image;
  const gray = to === "png" ? Buffer.allocUnsafe(width * height) : data;
  for (let index = 0; index < data.length; index += 4) {
    const value = Math.round((299 * data[index] + 587 * data[index + 1] + 114 * data[index + 2]) / 1000);
    if (to === "png") gray[index / 4] = value;
    else gray.fill(value, index, index + 3);
  }
  return { width, height, data: gray, colorType: to === "png" ? 0 : 6 };
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("file", { metavar: "入力" });
  parser.addOption("--out", { help: "出力先（省略時は標準出力）" });
  parser.addOption("--to", { choices: ["png", "bmp"], help: "変換先形式（省略時は出力先の拡張子から判定）" });
  parser.addFlag("--grayscale", { help: "BT.601 の輝度でグレースケール化する" });
  const args = parser.parse(argv);
  const to = args.to ?? extname(args.out ?? "").slice(1).toLowerCase();
  if (to !== "png" && to !== "bmp") throw new UsageError("--to または出力先の拡張子で png/bmp を指定してください");
  const input = readInput(undefined, args.file, io);
  let output;
  try {
    let image;
    if (input.subarray(0, 8).equals(PNG_SIGNATURE)) image = decodePng(input);
    else if (input.toString("latin1", 0, 2) === "BM") image = decodeBmp(input);
    else throw new Error("入力は PNG または BMP を指定してください");
    if (args.grayscale) image = grayscale(image, to);
    output = to === "png" ? encodePng(image) : encodeBmp(image);
  } catch (error) {
    throw new UsageError(error.message);
  }
  writeOutput(output, args.out, io);
  return 0;
}

module.exports = { name, help, usage, example, run };
