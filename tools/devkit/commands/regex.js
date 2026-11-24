"use strict";

const { ArgParser, UsageError } = require("../core/args");

const name = "regex";
const help = "正規表現の全マッチを表示し、文字列を置換する";
const usage = "devkit regex [--flags FLAGS] [--replace TEXT] [--json] pattern [text]";
const example = "devkit regex '([0-9]+)' 'ID: 123'";

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit regex", usage, description: help });
  parser.addPositional("pattern", { help: "JavaScript の正規表現" });
  parser.addPositional("text", { nargs: "?", help: "対象文字列（省略時は標準入力）" });
  parser.addOption("--flags", { def: "", help: "正規表現のフラグ（i / m / s / u）" });
  parser.addOption("--replace", { metavar: "TEXT", help: "置換文字列" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { pattern, text, flags, replace, json } = parser.parse(argv);
  if (!pattern) throw new UsageError("空のパターンは指定できません");
  if (/[^imsu]/.test(flags)) throw new UsageError("--flags に指定できるのは i / m / s / u です");

  let regex;
  try {
    regex = new RegExp(pattern, flags + "g");
  } catch (error) {
    throw new UsageError(`不正な正規表現: ${error.message}`);
  }
  const input = text ?? io.readStdin().toString("utf8").replace(/\r?\n$/, "");
  const matches = Array.from(input.matchAll(regex), (match) => ({
    match: match[0],
    index: match.index,
    groups: match.slice(1).map((group) => group ?? null),
  }));
  const result = { pattern, flags, matches, count: matches.length };
  if (replace !== undefined) result.replaced = input.replace(regex, replace);

  if (json) io.out(JSON.stringify(result) + "\n");
  else if (replace !== undefined) io.out(result.replaced + "\n");
  else if (matches.length === 0) io.out("マッチしませんでした\n");
  else {
    for (const [i, item] of matches.entries()) {
      io.out(`match ${i + 1}: ${item.match} (index ${item.index})\n`);
      for (const [j, group] of item.groups.entries()) {
        io.out(`  group ${j + 1}: ${group ?? "(なし)"}\n`);
      }
    }
  }
  return matches.length > 0 ? 0 : 1;
}

module.exports = { name, help, usage, example, run };
