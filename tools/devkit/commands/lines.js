"use strict";

const { randomInt } = require("node:crypto");
const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "lines";
const help = "行を重複除去・並べ替え・反転・シャッフルする";
const usage = "devkit lines [--sort] [--numeric] [--uniq] [--count] [--reverse] [--shuffle] [ファイル]";
const example = "devkit lines --sort --uniq --count items.txt";

function number(line) {
  return /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(line.trim()) ? Number(line) : NaN;
}

function compareText(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  for (const flag of ["sort", "numeric", "uniq", "count", "reverse", "shuffle"]) parser.addFlag("--" + flag);
  parser.addPositional("file", { nargs: "?" });
  const { file, sort, numeric, uniq, count, reverse, shuffle } = parser.parse(argv);
  if (shuffle && (sort || numeric || uniq || count || reverse)) {
    throw new UsageError("--shuffle は他の処理と併用できません");
  }
  const input = readInput(undefined, file === "-" ? undefined : file, io).toString("utf8");
  if (!input) return 0;
  let rows = input.split(/\r\n|\n|\r/);
  if (rows.at(-1) === "") rows.pop();
  const counts = new Map();
  if (uniq) {
    for (const row of rows) counts.set(row, (counts.get(row) ?? 0) + 1);
    rows = [...counts.keys()];
  }
  if (sort) {
    rows.sort(numeric ? (a, b) => {
      const x = number(a), y = number(b);
      const aNumeric = Number.isFinite(x), bNumeric = Number.isFinite(y);
      if (aNumeric && bNumeric) return x - y;
      if (aNumeric !== bNumeric) return aNumeric ? -1 : 1;
      return compareText(a, b);
    } : compareText);
  }
  if (reverse) rows.reverse();
  if (shuffle) {
    for (let i = rows.length - 1; i > 0; i--) {
      const j = randomInt(i + 1);
      [rows[i], rows[j]] = [rows[j], rows[i]];
    }
  }
  if (uniq && count) rows = rows.map((row) => `${counts.get(row)}\t${row}`);
  io.out(rows.join("\n") + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
