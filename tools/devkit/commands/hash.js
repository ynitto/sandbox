"use strict";

const { createHash, createHmac } = require("node:crypto");
const { ArgParser } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "hash";
const help = "ハッシュまたは HMAC を計算・照合する";
const usage = "devkit hash [--algo ALGO] [--hmac KEY] [--check EXPECTED] [--file FILE] [--json] [text]";
const example = "devkit hash --algo sha256 hello";

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit hash", usage, description: help });
  parser.addPositional("text", { nargs: "?", help: "入力文字列（最優先）" });
  parser.addOption("--file", { help: "入力ファイル（省略時は標準入力）" });
  parser.addOption("--algo", { def: "sha256", choices: ["md5", "sha1", "sha256", "sha512"], help: "アルゴリズム（既定値: sha256）" });
  parser.addOption("--hmac", { metavar: "KEY", help: "HMAC の鍵" });
  parser.addOption("--check", { metavar: "EXPECTED", help: "照合する16進ダイジェスト" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { text, file, algo, hmac: key, check, json } = parser.parse(argv);

  const input = readInput(text, file, io);
  const hmac = key !== undefined;
  const digest = (hmac ? createHmac(algo, key) : createHash(algo)).update(input).digest("hex");
  const result = { algo, hmac, digest };
  if (check !== undefined) result.match = digest === check.toLowerCase();

  if (json) io.out(JSON.stringify(result) + "\n");
  else if (check === undefined) io.out(digest + "\n");
  else io.out(result.match ? "一致しました\n" : "一致しませんでした\n");
  return result.match === false ? 1 : 0;
}

module.exports = { name, help, usage, example, run };
