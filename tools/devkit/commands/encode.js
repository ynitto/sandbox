"use strict";

const fs = require("node:fs");
const { dirname, join, resolve } = require("node:path");
const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");
const { boms, encodings, detectBom, detect, decode, encode } = require("../core/text-encoding");

const name = "encode";
const help = "文字エンコーディングと改行コードを判定・変換する";
const usage = "devkit encode [--check] [--json] [--from ENC] [--to ENC] [--lf|--crlf|--cr] [--bom|--no-bom] [--out ファイル] [--in-place] [--backup] [ファイル]";
const example = "devkit encode --to sjis --crlf report.txt";
const endings = { lf: "\n", crlf: "\r\n", cr: "\r" };

function lineEnding(text) {
  const seen = new Set();
  for (const [ending] of text.matchAll(/\r\n|\r|\n/g)) seen.add(ending);
  if (!seen.size) return "なし";
  if (seen.size > 1) return "混在";
  return Object.keys(endings).find((key) => seen.has(endings[key])).toUpperCase();
}

function replaceFile(path, bytes, mode, beforeCommit) {
  const dir = fs.mkdtempSync(join(dirname(path), ".devkit-encode-"));
  try {
    const staged = join(dir, "output");
    fs.writeFileSync(staged, bytes, { mode });
    fs.chmodSync(staged, mode);
    if (beforeCommit) beforeCommit();
    fs.renameSync(staged, path);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  for (const flag of ["check", "json", "lf", "crlf", "cr", "bom", "no-bom", "in-place", "backup"]) {
    parser.addFlag("--" + flag);
  }
  for (const option of ["from", "to"]) parser.addOption("--" + option, { choices: encodings, metavar: "ENC" });
  parser.addOption("--out");
  parser.addPositional("file", { nargs: "?" });
  const args = parser.parse(argv);
  const { file = "-", from, to, out, in_place: inPlace } = args;
  const newline = Object.keys(endings).filter((key) => args[key]);
  if (newline.length > 1) throw new UsageError("改行コードは1種類だけ指定してください");
  if (args.bom && args.no_bom) throw new UsageError("--bom と --no-bom は併用できません");
  if (out !== undefined && inPlace) throw new UsageError("--out と --in-place は併用できません");
  if (args.backup && !inPlace) throw new UsageError("--backup には --in-place が必要です");
  if (inPlace && file === "-") throw new UsageError("--in-place には入力ファイルが必要です");
  const input = readInput(undefined, file === "-" ? undefined : file, io);
  const bomEncoding = detectBom(input);
  const encoding = from ?? bomEncoding ?? detect(input);
  const bom = bomEncoding === encoding;
  const bytes = bom ? input.subarray(boms[encoding].length) : input;
  if (args.check || !(to || newline.length || args.bom || args.no_bom || out !== undefined || inPlace)) {
    const ending = lineEnding(decode(bytes, encoding, from !== undefined));
    const result = { file, encoding, bom, lineEnding: ending };
    io.out(args.json ? JSON.stringify(result) + "\n" :
      `ファイル: ${file}\nエンコーディング: ${encoding}\nBOM: ${bom ? "あり" : "なし"}\n改行コード: ${ending}\n`);
    return 0;
  }
  const target = to ?? encoding;
  if (args.bom && !Object.hasOwn(boms, target)) throw new UsageError("--bom は UTF-8 / UTF-16 で指定してください");
  let text = decode(bytes, encoding);
  if (newline.length) text = text.replace(/\r\n|\r|\n/g, endings[newline[0]]);
  let output = encode(text, target);
  if (Object.hasOwn(boms, target) && (args.bom || (bom && !args.no_bom))) {
    output = Buffer.concat([boms[target], output]);
  }
  try {
    if (inPlace) {
      const source = resolve(io.cwd, file);
      const destination = fs.realpathSync(source);
      const mode = fs.statSync(destination).mode & 0o777;
      const backup = source + ".bak";
      if (args.backup && backup === destination) throw new Error("バックアップ先と書き換え先が同じです");
      const saveBackup = args.backup ? () => replaceFile(backup, input, mode) : undefined;
      replaceFile(destination, output, mode, saveBackup);
    } else if (out !== undefined) fs.writeFileSync(resolve(io.cwd, out), output);
    else io.outBuffer(output);
  } catch (error) {
    throw new UsageError(`出力を書き込めません: ${error.message}`);
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
