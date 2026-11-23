"use strict";

const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { createHmac, timingSafeEqual } = require("node:crypto");
const { TextDecoder } = require("node:util");
const { ArgParser } = require("../core/args");
const { pyFloatStr, pyJsonStringify, pyRepr } = require("../core/py");
const { isValidZone, formatIso } = require("../core/tz");

const help = "JWT の中身を表示する。既定では署名を検証しない。";
const usage = "devkit jwt [--file FILE] [--tz TZ] [--secret SECRET] [token]";
const timeClaims = ["exp", "nbf", "iat", "auth_time"];
const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const whitespace = /[\p{White_Space}\u001c-\u001f]/gu;
const jsonToken = /("(?:[^"\\]|\\[^])*")|(-?Infinity|NaN|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?![a-zA-Z0-9.])/g;
const jsonString = /"(?:[^"\\]|\\[^])*"/g;

function decodeSegment(segment) {
  if (!/^[A-Za-z0-9_-]*={0,2}$/.test(segment)) {
    throw new Error("base64url に使用できない文字があります");
  }
  const unpadded = segment.replace(/=+$/, "");
  const remainder = unpadded.length % 4;
  if (segment.length - unpadded.length > (4 - remainder) % 4) {
    throw new Error("base64url のパディングが不正です");
  }
  if (remainder === 1) throw new Error("base64url の形式が不正です");
  return Buffer.from(unpadded, "base64url");
}

function formatFloat(value) {
  if (Object.is(value, -0)) return "-0.0";
  const magnitude = Math.abs(value);
  if ((magnitude > 0 && magnitude < 1e-4) || magnitude >= 1e16) {
    return value.toExponential().replace(/e([+-])([0-9])$/, "e$10$2");
  }
  return Number.isInteger(value) ? value + ".0" : String(value);
}

function decodeObject(segment, allowNonfiniteTimes = false) {
  const text = utf8.decode(decodeSegment(segment));
  if (text.startsWith("\uFEFF")) {
    throw new Error("Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)");
  }
  // Tag strings and number literals before parsing. This preserves integer
  // precision, the distinction between floats and integers, and numeric key order.
  const encoded = text.replace(jsonToken, (_, string, number) =>
    string === undefined ? JSON.stringify("n" + number) : '"s' + string.slice(1));
  const display = JSON.parse(encoded, (_, value) => {
    if (value && typeof value === "object" && !Array.isArray(value) &&
      Object.keys(value).some((key) => !key.startsWith("s"))) {
      throw new SyntaxError("Expecting property name enclosed in double quotes");
    }
    return value;
  });
  if (display === null || typeof display !== "object" || Array.isArray(display)) {
    throw new Error("JSON がオブジェクトではありません");
  }
  const value = Object.create(null);
  for (const [key, item] of Object.entries(display)) {
    if (typeof item !== "string") {
      value[key.slice(1)] = item;
      continue;
    }
    const literal = item.slice(1);
    const decoded = item.startsWith("s") ? literal : /^[+-]?[0-9]+$/.test(literal) ? BigInt(literal) : Number(literal);
    const name = key.slice(1);
    value[name] = decoded;
    if (allowNonfiniteTimes && timeClaims.includes(name) && typeof decoded === "number" && !Number.isFinite(decoded)) {
      display[key] = "s" + pyFloatStr(decoded);
    }
  }
  const formatted = pyJsonStringify(display, 2).replace(jsonString, (token) => {
    const tagged = JSON.parse(token);
    const literal = tagged.slice(1);
    if (tagged.startsWith("s")) {
      if (/[\uD800-\uDFFF]/u.test(literal)) throw new Error("UTF-8 に変換できないサロゲート文字があります");
      return JSON.stringify(literal);
    }
    if (/^-?[0-9]+$/.test(literal)) return String(BigInt(literal));
    const number = Number(literal);
    if (!Number.isFinite(number)) throw new Error("Out of range float values are not JSON compliant: " + pyFloatStr(number));
    return formatFloat(number);
  });
  return [value, formatted];
}

function instantMicroseconds(seconds) {
  const integral = Math.trunc(seconds);
  const micro = (seconds - integral) * 1e6;
  const floor = Math.floor(micro);
  const rounded = micro - floor === 0.5 && floor % 2 === 0 ? floor : Math.round(micro);
  return BigInt(integral) * 1000000n + BigInt(rounded);
}

function showTimes(payload, zone, now, io) {
  for (const claim of timeClaims) {
    if (!Object.hasOwn(payload, claim)) continue;
    let value = payload[claim];
    if (typeof value === "string") {
      const literal = value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "");
      if (/^[+-]?[0-9](?:_?[0-9])*$/.test(literal)) value = BigInt(literal.replace(/_/g, ""));
    }
    if (typeof value !== "number" && typeof value !== "bigint") {
      io.out(`${claim}: 数値の UNIX 時刻ではありません\n`);
      continue;
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      io.out(`${claim}: 有限の UNIX 時刻ではありません (${pyFloatStr(value)})\n`);
      continue;
    }
    const seconds = Number(value);
    try {
      if (seconds < -62135596800 || seconds >= 253402300800) throw new RangeError();
      const iso = formatIso(instantMicroseconds(seconds), zone);
      if (!/^(?!0000)[0-9]{4}-/.test(iso)) throw new RangeError();
      io.out(`${claim}: ${iso} (${zone})\n`);
    } catch {
      io.out(`${claim}: 表示可能な日時の範囲外です\n`);
    }
    if (claim === "exp" && value <= now) io.out("警告: 期限切れ (exp)\n");
    if (claim === "nbf" && value > now) io.out("警告: まだ有効になっていない (nbf)\n");
  }
}

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit jwt", usage, description: help });
  parser.addPositional("token", { nargs: "?", help: "JWT 文字列（最優先）" });
  parser.addOption("--file", { key: "file", help: "JWT を読むファイル（省略時は標準入力）" });
  parser.addOption("--tz", { key: "tz", def: "UTC", help: "時刻表示の IANA タイムゾーン（既定値: UTC）" });
  parser.addOption("--secret", { key: "secret", help: "HS256 署名検証用の秘密文字列" });
  const { token, file, tz, secret } = parser.parse(argv);
  if (secret === "") {
    io.err("--secret に空文字列は指定できません\n");
    return 1;
  }
  let input;
  try {
    if (/^[+-]/.test(tz) || !isValidZone(tz)) throw new Error(pyRepr("No time zone found with key " + tz));
    input = token ?? utf8.decode(file === undefined ? io.readStdin() : readFileSync(resolve(io.cwd, file)));
  } catch (error) {
    io.err(`入力またはタイムゾーンのエラー: ${error.message}\n`);
    return 1;
  }
  const parts = input.replace(whitespace, "").split(".");
  if (parts.length !== 3) {
    io.err("不正な JWT: パート数は 3 である必要があります\n");
    return 2;
  }
  let part = "Header";
  let header;
  let headerJson;
  let payload;
  let payloadJson;
  let signature;
  try {
    [header, headerJson] = decodeObject(parts[0]);
    part = "Payload";
    [payload, payloadJson] = decodeObject(parts[1], true);
    part = "署名";
    signature = decodeSegment(parts[2]);
  } catch (error) {
    io.err(`不正な JWT (${part}): ${error.message}\n`);
    return 2;
  }
  io.out(`Header:\n${headerJson}\nPayload:\n${payloadJson}\n`);
  showTimes(payload, tz, io.nowSeconds ?? Date.now() / 1000, io);
  io.out(`署名: ${signature.length ? "あり" : "なし"} (${signature.length} バイト)\n`);
  if (secret === undefined) return 0;
  if (header.alg !== "HS256") {
    io.out("署名検証: HS256 以外は検証できません\n");
    return 3;
  }
  const expected = createHmac("sha256", secret).update(parts.slice(0, 2).join(".")).digest();
  const verified = signature.length === expected.length && timingSafeEqual(signature, expected);
  io.out(verified ? "署名検証: OK\n" : "署名検証: NG\n");
  return verified ? 0 : 3;
}

module.exports = { name: "jwt", help, usage, run };
