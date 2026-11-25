"use strict";

const { UsageError } = require("./args");

const boms = {
  utf8: Buffer.from("efbbbf", "hex"),
  "utf-16le": Buffer.from("fffe", "hex"),
  "utf-16be": Buffer.from("feff", "hex"),
};
const labels = { utf8: "utf-8", sjis: "shift_jis", "euc-jp": "euc-jp", "utf-16le": "utf-16le", "utf-16be": "utf-16be" };
const encodings = ["utf8", "sjis", "euc-jp", "utf-16le", "utf-16be", "ascii"];
const reverseTables = new Map();

function detectBom(bytes) {
  return Object.keys(boms).find((encoding) => bytes.subarray(0, boms[encoding].length).equals(boms[encoding]));
}

function decode(bytes, encoding, fatal = true) {
  try {
    if (encoding === "ascii") {
      if (bytes.some((byte) => byte > 127)) throw new Error();
      return bytes.toString("ascii");
    }
    return new TextDecoder(labels[encoding], { fatal, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new UsageError(`${encoding} としてデコードできません`);
  }
}

// BOM の無い UTF-16 は、ASCII 文字の並びに現れる NUL バイトの偏りで見分ける
// （LE は奇数位置、BE は偶数位置に 0 が並ぶ）。日本語だけの文章では判定できない。
function detectUtf16(bytes) {
  const limit = Math.min(bytes.length - (bytes.length % 2), 8192);
  if (limit < 4) return undefined;
  const pairs = limit / 2;
  let evenZeros = 0, oddZeros = 0;
  for (let i = 0; i < limit; i += 2) {
    if (bytes[i] === 0) evenZeros++;
    if (bytes[i + 1] === 0) oddZeros++;
  }
  if (oddZeros / pairs >= 0.3 && evenZeros / pairs < 0.05) return "utf-16le";
  if (evenZeros / pairs >= 0.3 && oddZeros / pairs < 0.05) return "utf-16be";
  return undefined;
}

function detect(bytes) {
  const utf16 = detectUtf16(bytes);
  if (utf16) return utf16;
  if (bytes.every((byte) => byte < 128)) return "ascii";
  try {
    decode(bytes, "utf8");
    return "utf8";
  } catch {
    // UTF-8 に失敗した場合は日本語の候補を比較する。
  }
  const bad = (text) => [...text.matchAll(/\ufffd/g)].length;
  const score = (text) => {
    let result = 0;
    for (const char of text) {
      if (/[ぁ-ゖァ-ヺ]/u.test(char)) result += 4;
      else if (/\p{Script=Han}/u.test(char)) result += 3;
      else if (/[｡-ﾟ]/u.test(char)) result += 0.25;
      else if (/[^\x00-\x7f]/u.test(char)) result -= 1;
    }
    return result;
  };
  const sjis = decode(bytes, "sjis", false), euc = decode(bytes, "euc-jp", false);
  const difference = bad(sjis) - bad(euc);
  return difference > 0 || (difference === 0 && score(euc) > score(sjis)) ? "euc-jp" : "sjis";
}

function reverseTable(encoding) {
  if (reverseTables.has(encoding)) return reverseTables.get(encoding);
  const table = new Map();
  const decoder = new TextDecoder(labels[encoding], { fatal: true, ignoreBOM: true });
  function add(...codes) {
    const bytes = Buffer.from(codes);
    try {
      const text = decoder.decode(bytes);
      if ([...text].length === 1 && !table.has(text)) table.set(text, bytes);
    } catch {
      // この組み合わせは文字に割り当てられていない。
    }
  }
  for (let byte = 0; byte <= 0xff; byte++) add(byte);
  if (encoding === "sjis") {
    for (let lead = 0x81; lead <= 0xfc; lead++) {
      if (lead > 0x9f && lead < 0xe0) continue;
      for (let trail = 0x40; trail <= 0xfc; trail++) {
        if (trail !== 0x7f) add(lead, trail);
      }
    }
  } else {
    for (let byte = 0xa1; byte <= 0xdf; byte++) add(0x8e, byte);
    for (let lead = 0xa1; lead <= 0xfe; lead++) {
      for (let trail = 0xa1; trail <= 0xfe; trail++) {
        add(lead, trail);
        add(0x8f, lead, trail);
      }
    }
  }
  reverseTables.set(encoding, table);
  return table;
}

function encode(text, encoding) {
  if (encoding === "utf8") return Buffer.from(text, "utf8");
  if (encoding === "utf-16le") return Buffer.from(text, "utf16le");
  if (encoding === "utf-16be") return Buffer.from(text, "utf16le").swap16();
  const unrepresentable = (char) => new UsageError(`${encoding} で表現できない文字があります: ${JSON.stringify(char)}`);
  if (encoding === "ascii") {
    const invalid = /[^\x00-\x7f]/u.exec(text);
    if (invalid) throw unrepresentable(invalid[0]);
    return Buffer.from(text, "ascii");
  }
  const table = reverseTable(encoding);
  const output = [];
  for (const char of text) {
    const bytes = table.get(char);
    if (!bytes) throw unrepresentable(char);
    output.push(bytes);
  }
  return Buffer.concat(output);
}

module.exports = { boms, encodings, detectBom, detect, decode, encode };
