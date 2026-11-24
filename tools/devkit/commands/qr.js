"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { integer, readInput } = require("../core/command-input");
const { writeOutput } = require("../core/command-output");
const { encodeQr } = require("../core/qr");

const name = "qr";
const help = "テキストから QR コードを SVG で生成する";
const usage = "devkit qr [--level L|M|Q|H] [--out ファイル.svg] [--border N] [テキスト]";
const example = 'devkit qr "HELLO WORLD" --out hello.svg';

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("text", { metavar: "テキスト", nargs: "?", help: "省略時は標準入力" });
  parser.addOption("--level", { choices: ["L", "M", "Q", "H"], def: "M", help: "誤り訂正レベル（既定値: M）" });
  parser.addOption("--out", { help: "SVG の出力先（省略時は標準出力）" });
  parser.addOption("--border", { metavar: "N", type: integer(0, Math.floor((Number.MAX_SAFE_INTEGER - 57) / 2)), def: 4, help: "余白のモジュール数（既定値: 4）" });
  const { text, level, out, border } = parser.parse(argv);
  const input = readInput(text, undefined, io).toString("utf8");
  let qr;
  try {
    qr = encodeQr(input, level);
  } catch (error) {
    throw new UsageError(error.message);
  }
  const side = qr.size + 2 * border;
  const path = [];
  for (let y = 0; y < qr.size; y++) {
    for (let x = 0; x < qr.size; x++) {
      if (qr.modules[y][x]) path.push(`M${x + border} ${y + border}h1v1h-1z`);
    }
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${side}" height="${side}" viewBox="0 0 ${side} ${side}" shape-rendering="crispEdges">\n` +
    `  <rect width="100%" height="100%" fill="white"/>\n  <path fill="black" d="${path.join("")}"/>\n</svg>\n`;
  writeOutput(svg, out, io);
  return 0;
}

module.exports = { name, help, usage, example, run };
