"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");
const { parseYaml, formatYaml } = require("../core/yaml");

const name = "yaml";
const help = "JSON と基本的な YAML を相互変換する";
const usage = "devkit yaml [--to json|yaml] [ファイル]";
const example = "devkit yaml config.yaml";

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addOption("--to", { choices: ["json", "yaml"] });
  parser.addPositional("file", { nargs: "?" });
  const { to, file } = parser.parse(argv);
  const input = readInput(undefined, file === "-" ? undefined : file, io).toString("utf8").replace(/^\ufeff/, "");
  if (to !== "json") {
    let value;
    try {
      value = JSON.parse(input);
    } catch (error) {
      if (to === "yaml") throw new UsageError(`JSON を読み込めません: ${error.message}`);
    }
    if (value !== undefined) {
      io.out(formatYaml(value));
      return 0;
    }
  }
  // JSON はそのままでも YAML の入力として使える。
  let value;
  try {
    value = JSON.parse(input);
  } catch {
    value = parseYaml(input);
  }
  io.out(JSON.stringify(value, null, 2) + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
