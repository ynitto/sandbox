"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "b64";
const help = "Base64 をエンコードまたはデコードする";
const usage = "devkit b64 [--decode] [--url] [--file FILE] [text]";
const example = "devkit b64 --decode aGVsbG8=";

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit b64", usage, description: help });
  parser.addPositional("text", { nargs: "?", help: "入力文字列（最優先）" });
  parser.addOption("--file", { help: "入力ファイル（省略時は標準入力）" });
  parser.addFlag("--decode", { help: "Base64 をデコードする" });
  parser.addFlag("--url", { help: "base64url のアルファベットを使う" });
  const { text, file, decode, url } = parser.parse(argv);
  const input = readInput(text, file, io);

  if (!decode) {
    const encoded = input.toString("base64");
    io.out((url ? encoded.replace(/\+/g, "-").replace(/\//g, "_") : encoded) + "\n");
    return 0;
  }

  const encoded = input.toString("utf8").replace(/[\s\p{White_Space}]/gu, "");
  const alphabet = url ? /[^A-Za-z0-9_=-]/ : /[^A-Za-z0-9+/=]/;
  if (alphabet.test(encoded)) throw new UsageError("Base64 に使用できない文字があります");
  const unpadded = encoded.replace(/=+$/, "");
  const remainder = unpadded.length % 4;
  const padding = encoded.length - unpadded.length;
  if (unpadded.includes("=") || (padding > 0 && (padding > 2 || padding !== (4 - remainder) % 4))) {
    throw new UsageError("Base64 のパディングが不正です");
  }
  if (remainder === 1) throw new UsageError("Base64 の長さが不正です");
  io.outBuffer(Buffer.from(unpadded, url ? "base64url" : "base64"));
  return 0;
}

module.exports = { name, help, usage, example, run };
