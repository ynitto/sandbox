"use strict";

const { ArgParser, UsageError } = require("../core/args");
const { readInput } = require("../core/command-input");

const name = "b32";
const help = "Base32・Base58・16進数をエンコードまたはデコードする";
const usage = "devkit b32 [--decode] [--format base32|base58|hex] [--file FILE] [text]";
const example = "devkit b32 hello";
const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function encode32(input) {
  let bits = 0, value = 0;
  let output = "";
  for (const byte of input) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      output += BASE32[(value >>> bits) & 31];
    }
  }
  if (bits) output += BASE32[(value << (5 - bits)) & 31];
  return output.padEnd(Math.ceil(output.length / 8) * 8, "=");
}

function decode32(input) {
  if (!/^[A-Z2-7]*=*$/i.test(input)) throw new UsageError("Base32 に使用できない文字があります");
  const body = input.replace(/=+$/, "").toUpperCase();
  const remainder = body.length % 8;
  if (![0, 2, 4, 5, 7].includes(remainder)) throw new UsageError("Base32 の長さが不正です");
  if (input.length !== body.length && input.length - body.length !== (8 - remainder) % 8) {
    throw new UsageError("Base32 のパディングが不正です");
  }
  const output = Buffer.alloc(Math.floor(body.length * 5 / 8));
  let value = 0, bits = 0, offset = 0;
  for (const char of body) {
    value = (value << 5) | BASE32.indexOf(char);
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      output[offset++] = value >>> bits;
    }
  }
  if (value & ((1 << bits) - 1)) throw new UsageError("Base32 の末尾ビットが不正です");
  return output;
}

// Base58 は桁配列への繰り上げ方式で変換する（入力全体を BigInt 化すると
// 大きな入力で極端に遅くなるため）。
function encode58(input) {
  let zeros = 0;
  while (zeros < input.length && input[zeros] === 0) zeros++;
  if (zeros === input.length) return "1".repeat(zeros);
  const digits = [0];
  for (let i = zeros; i < input.length; i++) {
    let carry = input[i];
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8;
      digits[j] = carry % 58;
      carry = Math.floor(carry / 58);
    }
    while (carry) {
      digits.push(carry % 58);
      carry = Math.floor(carry / 58);
    }
  }
  let output = "1".repeat(zeros);
  for (let i = digits.length - 1; i >= 0; i--) output += BASE58[digits[i]];
  return output;
}

function decode58(input) {
  const zeros = input.match(/^1*/)[0].length;
  if (zeros === input.length) return Buffer.alloc(zeros);
  const bytes = [0];
  for (const char of input.slice(zeros)) {
    const digit = BASE58.indexOf(char);
    if (digit < 0) throw new UsageError("Base58 に使用できない文字があります");
    let carry = digit;
    for (let j = 0; j < bytes.length; j++) {
      carry += bytes[j] * 58;
      bytes[j] = carry & 0xff;
      carry >>= 8;
    }
    while (carry) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  return Buffer.concat([Buffer.alloc(zeros), Buffer.from(bytes.reverse())]);
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("text", { nargs: "?", help: "入力文字列（最優先）" });
  parser.addOption("--file", { help: "入力ファイル（省略時は標準入力）" });
  parser.addFlag("--decode", { help: "デコードする" });
  parser.addOption("--format", { choices: ["base32", "base58", "hex"], def: "base32" });
  const { text, file, decode, format } = parser.parse(argv);
  const input = readInput(text, file, io);
  if (!decode) {
    const output = format === "base32" ? encode32(input) : format === "base58" ? encode58(input) : input.toString("hex");
    io.out(output + "\n");
    return 0;
  }
  const encoded = input.toString("utf8").replace(/\s/gu, "");
  if (format === "base32") io.outBuffer(decode32(encoded));
  else if (format === "base58") io.outBuffer(decode58(encoded));
  else {
    if (!/^(?:[\da-f]{2})*$/i.test(encoded)) throw new UsageError("16進数は偶数桁で指定してください");
    io.outBuffer(Buffer.from(encoded, "hex"));
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
