"use strict";

// 元ツール: tools/env-diff (env_diff.py)
// 2つの .env ファイルを比較する。既定では値を表示しない。

const fs = require("fs");
const path = require("path");
const { ArgParser, UsageError } = require("../core/args");
const { pyRepr, pyJsonStringify } = require("../core/py");

function parseEnvText(text, pathLabel) {
  const values = new Map();
  const linesByKey = new Map();
  const lines = text.split(/\r\n|\r|\n/);
  lines.forEach((rawLine, index) => {
    const number = index + 1;
    let line = rawLine.trim();
    if (!line || line.startsWith("#")) return;
    if (line.startsWith("export ")) line = line.slice("export ".length);
    const eq = line.indexOf("=");
    const key = (eq === -1 ? line : line.slice(0, eq)).trim();
    if (eq === -1 || !key || /\s/.test(key)) {
      throw new Error(`${pathLabel}: ${number} 行目が KEY=VALUE 形式ではありません`);
    }
    let value = line.slice(eq + 1).trim();
    if (
      value.length >= 2 &&
      (value[0] === '"' || value[0] === "'") &&
      value[value.length - 1] === value[0]
    ) {
      value = value.slice(1, -1);
    }
    values.set(key, value);
    if (!linesByKey.has(key)) linesByKey.set(key, []);
    linesByKey.get(key).push(number);
  });
  const duplicates = [...linesByKey.keys()]
    .sort()
    .filter((key) => linesByKey.get(key).length > 1)
    .map((key) => ({ key, lines: linesByKey.get(key) }));
  return { values, duplicates };
}

const STRERROR = {
  ENOENT: "No such file or directory",
  EISDIR: "Is a directory",
  EACCES: "Permission denied",
  ENOTDIR: "Not a directory",
};

function readInput(pathLabel, cwd) {
  let buffer;
  try {
    buffer = fs.readFileSync(path.resolve(cwd, pathLabel));
  } catch (error) {
    throw new UsageError(`${pathLabel}: ${STRERROR[error.code] || error.message}`);
  }
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    throw new UsageError(`${pathLabel}: UTF-8 として読み込めません`);
  }
  if (text.startsWith("\uFEFF")) text = text.slice(1); // utf-8-sig
  try {
    return parseEnvText(text, pathLabel);
  } catch (error) {
    throw new UsageError(error.message);
  }
}

function describeValue(value, showValues) {
  const result = { empty: value === "" };
  if (showValues) result.value = value;
  return result;
}

function compare(a, b, duplicatesA, duplicatesB, showValues) {
  const keysA = new Set(a.keys());
  const keysB = new Set(b.keys());
  return {
    only_in_a: [...keysA]
      .filter((key) => !keysB.has(key))
      .sort()
      .map((key) => ({ key, ...describeValue(a.get(key), showValues) })),
    only_in_b: [...keysB]
      .filter((key) => !keysA.has(key))
      .sort()
      .map((key) => ({ key, ...describeValue(b.get(key), showValues) })),
    different: [...keysA]
      .filter((key) => keysB.has(key) && a.get(key) !== b.get(key))
      .sort()
      .map((key) => ({
        key,
        a: describeValue(a.get(key), showValues),
        b: describeValue(b.get(key), showValues),
      })),
    duplicates_a: duplicatesA,
    duplicates_b: duplicatesB,
  };
}

function formatValue(entry) {
  if (entry.empty) return " (空)";
  if ("value" in entry) return ` = ${pyRepr(entry.value)}`;
  return "";
}

function printReport(result, io) {
  const labels = {
    only_in_a: "A にだけあるキー",
    only_in_b: "B にだけあるキー",
    different: "値が違うキー",
    duplicates_a: "A の重複キー",
    duplicates_b: "B の重複キー",
  };
  for (const [group, entries] of Object.entries(result)) {
    if (entries.length === 0) continue;
    io.out(labels[group] + "\n");
    for (const entry of entries) {
      let suffix;
      if (group === "different") {
        const sides = ["a", "b"]
          .map((side) => [side.toUpperCase(), formatValue(entry[side])])
          .filter(([, detail]) => detail)
          .map(([label, detail]) => label + detail);
        suffix = sides.length ? ` [${sides.join(", ")}]` : "";
      } else if ("lines" in entry) {
        suffix = ` (${entry.lines.join(", ")} 行目)`;
      } else {
        suffix = formatValue(entry);
      }
      io.out(`  ${entry.key}${suffix}\n`);
    }
  }
}

function run(argv, io) {
  const parser = new ArgParser({
    prog: "devkit env",
    usage: "devkit env [--show-values] [--json] ファイルA ファイルB",
    description: "Compare two .env files. Values are hidden by default to keep secrets out of CI logs.",
  });
  parser.addFlag("--show-values", { key: "show_values", help: "差分に値を表示する" });
  parser.addFlag("--json", { key: "json", help: "JSON 形式で出力する" });
  parser.addPositional("file_a", { metavar: "ファイルA" });
  parser.addPositional("file_b", { metavar: "ファイルB" });
  const args = parser.parse(argv);

  const a = readInput(args.file_a, io.cwd);
  const b = readInput(args.file_b, io.cwd);
  const result = compare(a.values, b.values, a.duplicates, b.duplicates, args.show_values);
  const hasDifferences = Object.values(result).some((entries) => entries.length > 0);
  if (args.json) {
    io.out(pyJsonStringify(result, 2) + "\n");
  } else if (hasDifferences) {
    printReport(result, io);
  } else {
    io.out("差分なし\n");
  }
  return hasDifferences ? 1 : 0;
}

module.exports = {
  name: "env",
  help: ".env ファイル同士を比較する（値は既定で隠す）",
  usage: "devkit env [--show-values] [--json] ファイルA ファイルB",
  example: "devkit env <(printf 'APP=demo\\n') <(printf 'APP=demo\\n')",
  run,
};
