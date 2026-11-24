"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { integer, readInput } = require("../core/command-input");

const name = "json";
const help = "JSON を整形・検証し、指定したパスの値を抽出する";
const usage = "devkit json [--indent N] [--minify] [--check] [--path PATH] [--file FILE] [text]";
const example = "devkit json --path user.name '{\"user\":{\"name\":\"sample\"}}'";

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit json", usage, description: help });
  parser.addPositional("text", { nargs: "?", help: "入力文字列（最優先）" });
  parser.addOption("--file", { help: "入力ファイル（省略時は標準入力）" });
  parser.addOption("--indent", { metavar: "N", def: 2, type: integer(0, 10), help: "インデントの空白数（既定値: 2）" });
  parser.addFlag("--minify", { help: "空白を除いて出力する" });
  parser.addFlag("--check", { help: "JSON を検証する" });
  parser.addOption("--path", { help: "抽出する値のドット区切りパス" });
  const { text, file, indent, minify, check, path } = parser.parse(argv);

  const input = readInput(text, file, io);
  let value;
  try {
    value = JSON.parse(input.toString("utf8"));
  } catch (error) {
    throw new UsageError(`不正な JSON: ${error.message}`);
  }
  if (check) {
    io.out("valid\n");
    return 0;
  }
  if (path !== undefined) {
    for (const segment of path.split(".")) {
      const key = Array.isArray(value) && /^\d+$/.test(segment) ? Number(segment) : segment;
      if (value === null || typeof value !== "object" ||
          (Array.isArray(value) && typeof key !== "number") || !Object.hasOwn(value, key)) {
        io.err(`JSON パスが見つかりません: ${path}\n`);
        return 1;
      }
      value = value[key];
    }
  }

  const output = path !== undefined && typeof value === "string"
    ? value
    : JSON.stringify(value, null, minify ? 0 : indent);
  io.out(output + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
