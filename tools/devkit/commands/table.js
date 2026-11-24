"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "table";
const help = "CSV または TSV を Markdown・ASCII 表に整形する";
const usage = "devkit table [--format md|ascii] [--file FILE] [ファイル]";
const example = "devkit table --format ascii data.csv";

function parse(input, delimiter) {
  const rows = [];
  let row = [], cell = "", quoted = false, closed = false, started = false;
  const finishCell = () => {
    row.push(cell);
    cell = "";
    closed = false;
    started = false;
  };
  for (let i = 0; i < input.length; i++) {
    const char = input[i];
    if (quoted) {
      if (char === '"' && input[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (char === '"') {
        quoted = false;
        closed = true;
      } else cell += char;
    } else if (char === delimiter) finishCell();
    else if (char === "\r" || char === "\n") {
      if (char === "\r" && input[i + 1] === "\n") i++;
      finishCell();
      rows.push(row);
      row = [];
    } else if (closed) throw new UsageError("引用符の後には区切り文字が必要です");
    else if (char === '"' && delimiter === "," && !started) {
      quoted = true;
      started = true;
    } else {
      cell += char;
      started = true;
    }
  }
  if (quoted) throw new UsageError("引用符が閉じていません");
  if (row.length || started || closed) {
    finishCell();
    rows.push(row);
  }
  return rows;
}

function width(text) {
  let columns = 0;
  for (const char of text) {
    if (/[\p{M}\p{Cf}]/u.test(char)) continue;
    columns += /[\u1100-\u115f\u2329\u232a\u2e80-\u303e\u3040-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\ufe19\ufe30-\ufe6f\uff01-\uff60\uffe0-\uffe6\u{1f300}-\u{1faff}\u{20000}-\u{3fffd}]/u.test(char) ? 2 : 1;
  }
  return columns;
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addOption("--format", { choices: ["md", "ascii"], def: "md" });
  parser.addOption("--file", { help: "入力ファイル（省略時は標準入力）" });
  parser.addPositional("path", { nargs: "?" });
  const { format, file, path } = parser.parse(argv);
  const source = path ?? file;
  const input = readInput(undefined, source === "-" ? undefined : source, io).toString("utf8").replace(/^\ufeff/, "");
  if (!input.trim()) throw new UsageError("入力が空です");
  const rows = parse(input, input.includes("\t") ? "\t" : ",");
  const columns = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const cells = rows.map((row) => Array.from({ length: columns }, (_, i) => row[i] ?? ""));
  let output;
  if (format === "md") {
    const render = (row) => "| " + row.map((cell) => cell.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\r\n|\r|\n/g, "<br>")).join(" | ") + " |";
    output = [render(cells[0]), render(Array(columns).fill("---")), ...cells.slice(1).map(render)];
  } else {
    const flat = cells.map((row) => row.map((cell) => cell.replace(/\r\n|\r|\n/g, "\\n")));
    const widths = Array(columns).fill(0);
    for (const row of flat) row.forEach((cell, i) => { widths[i] = Math.max(widths[i], width(cell)); });
    const border = "+" + widths.map((size) => "-".repeat(size + 2)).join("+") + "+";
    const render = (row) => "| " + row.map((cell, i) => cell + " ".repeat(widths[i] - width(cell))).join(" | ") + " |";
    output = [border, render(flat[0]), border, ...flat.slice(1).map(render)];
    if (flat.length > 1) output.push(border);
  }
  io.out(output.join("\n") + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
