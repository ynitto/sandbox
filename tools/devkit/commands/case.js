"use strict";

const { ArgParser } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "case";
const help = "テキストを snake・camel などの表記に変換する";
const usage = "devkit case [--to 形式] [テキスト]";
const example = "devkit case --to camel hello_world_example";
const formats = ["snake", "camel", "pascal", "kebab", "upper-snake", "lower", "upper"];

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addOption("--to", { choices: formats });
  parser.addPositional("text", { nargs: "?" });
  const { to, text } = parser.parse(argv);
  const input = readInput(text, undefined, io).toString("utf8").replace(/(?:\r\n|\r|\n)$/, "");
  const words = input
    .replace(/([\p{Ll}\p{N}])(\p{Lu})/gu, "$1 $2")
    .replace(/(\p{Lu})(\p{Lu}\p{Ll})/gu, "$1 $2")
    .toLowerCase().match(/[\p{L}\p{N}\p{M}]+/gu) ?? [];
  const capitalized = words.map((word) => word.replace(/^./u, (char) => char.toUpperCase()));
  const results = {
    snake: words.join("_"),
    camel: (words[0] ?? "") + capitalized.slice(1).join(""),
    pascal: capitalized.join(""),
    kebab: words.join("-"),
    "upper-snake": words.join("_").toUpperCase(),
    lower: input.toLowerCase(),
    upper: input.toUpperCase(),
  };
  io.out((to ? results[to] : formats.map((format) => `${format}: ${results[format]}`).join("\n")) + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
