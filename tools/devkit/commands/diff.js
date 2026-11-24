"use strict";

const { ArgParser } = require("../core/args");
const { integer, readInput } = require("../core/command-input");

const name = "diff";
const help = "2つのファイルを行単位で比較し unified diff を表示する";
const usage = "devkit diff [--context N] <ファイルA> <ファイルB>";
const example = "devkit diff old.txt new.txt";

function editMiddle(a, b, emit) {
  if (!a.length || !b.length) {
    for (const line of a) emit("-", line);
    for (const line of b) emit("+", line);
    return;
  }
  const lengths = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lengths[i][j] = a[i] === b[j] ? lengths[i + 1][j + 1] + 1
        : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      emit(" ", a[i++]);
      j++;
    } else if (i < a.length && (j === b.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      emit("-", a[i++]);
    } else emit("+", b[j++]);
  }
}

function edits(a, b) {
  const rows = [];
  let oldLine = 1, newLine = 1;
  const emit = (kind, line) => {
    rows.push({ kind, line, oldLine, newLine });
    if (kind !== "+") oldLine++;
    if (kind !== "-") newLine++;
  };
  let start = 0, endA = a.length, endB = b.length;
  while (start < endA && start < endB && a[start] === b[start]) emit(" ", a[start++]);
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  editMiddle(a.slice(start, endA), b.slice(start, endB), emit);
  for (let i = endA; i < a.length; i++) emit(" ", a[i]);
  return rows;
}

function hunks(rows, context) {
  const ranges = [];
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].kind === " ") continue;
    const start = Math.max(0, i - context);
    const end = Math.min(rows.length, i + context + 1);
    const previous = ranges.at(-1);
    if (previous && start <= previous[1]) previous[1] = end;
    else ranges.push([start, end]);
  }
  return ranges;
}

function range(start, count) {
  if (count === 0) return `${start - 1},0`;
  return count === 1 ? String(start) : `${start},${count}`;
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addOption("--context", { type: integer(0), def: 3 });
  parser.addPositional("a", { metavar: "ファイルA" });
  parser.addPositional("b", { metavar: "ファイルB" });
  const { a, b, context } = parser.parse(argv);
  const read = (file) => readInput(undefined, file === "-" ? undefined : file, io).toString("utf8");
  const before = read(a);
  const after = a === b ? before : read(b);
  if (before === after) return 0;
  const lines = (text) => text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const rows = edits(lines(before), lines(after));
  io.out(`--- a/${a}\n+++ b/${b}\n`);
  for (const [start, end] of hunks(rows, context)) {
    const hunk = rows.slice(start, end);
    const oldRange = range(hunk[0].oldLine, hunk.filter(({ kind }) => kind !== "+").length);
    const newRange = range(hunk[0].newLine, hunk.filter(({ kind }) => kind !== "-").length);
    io.out(`@@ -${oldRange} +${newRange} @@\n`);
    for (const { kind, line } of hunk) {
      io.out(kind + line);
      if (!line.endsWith("\n")) io.out("\n\\ No newline at end of file\n");
    }
  }
  return 1;
}

module.exports = { name, help, usage, example, run };
