"use strict";

// Python の挙動を再現するための小さなヘルパー群。
// 各コマンドは元の Python 実装の出力形式・書式をそのまま再現する。

// Python の repr(str) に近い表現を返す。
// 単一引用符を基本とし、中に単一引用符だけがあり二重引用符が無い場合は二重引用符を使う。
function pyRepr(value) {
  const s = String(value);
  const hasSingle = s.includes("'");
  const hasDouble = s.includes('"');
  const quote = hasSingle && !hasDouble ? '"' : "'";
  let out = quote;
  for (const ch of s) {
    if (ch === quote || ch === "\\") {
      out += "\\" + ch;
    } else if (ch === "\n") {
      out += "\\n";
    } else if (ch === "\r") {
      out += "\\r";
    } else if (ch === "\t") {
      out += "\\t";
    } else {
      const code = ch.codePointAt(0);
      if (code < 0x20 || code === 0x7f) {
        out += "\\x" + code.toString(16).padStart(2, "0");
      } else {
        out += ch;
      }
    }
  }
  return out + quote;
}

// Python の str(float) に近い表現。inf / -inf / nan の表記を合わせる。
function pyFloatStr(value) {
  if (Number.isNaN(value)) return "nan";
  if (value === Infinity) return "inf";
  if (value === -Infinity) return "-inf";
  return String(value);
}

// Python の json.dumps に近い JSON 文字列化。
// - BigInt はそのまま10進数として出力する（Python の任意精度整数に相当）。
// - 浮動小数点は JS の最短表現を使う（Python の repr とほぼ一致する）。
// - indent を指定すると Python と同じく ", " ではなく改行区切りになる。
function pyJsonStringify(value, indent) {
  const seen = new Set();
  function encode(v, level) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "number") {
      if (!Number.isFinite(v)) {
        throw new TypeError("Out of range float values are not JSON compliant");
      }
      return String(v);
    }
    if (typeof v === "string") return JSON.stringify(v);
    if (typeof v === "object") {
      if (seen.has(v)) throw new TypeError("circular structure");
      seen.add(v);
      try {
        const pad = indent ? " ".repeat(indent * (level + 1)) : "";
        const closePad = indent ? " ".repeat(indent * level) : "";
        const sep = indent ? ",\n" : ", ";
        const kvSep = indent ? ": " : ": ";
        if (Array.isArray(v)) {
          if (v.length === 0) return "[]";
          const items = v.map((item) => pad + encode(item, level + 1));
          return indent
            ? "[\n" + items.join(sep) + "\n" + closePad + "]"
            : "[" + v.map((item) => encode(item, level + 1)).join(sep) + "]";
        }
        const keys = Object.keys(v);
        if (keys.length === 0) return "{}";
        const items = keys.map(
          (key) => pad + JSON.stringify(key) + kvSep + encode(v[key], level + 1)
        );
        return indent
          ? "{\n" + items.join(sep) + "\n" + closePad + "}"
          : "{" + keys.map((key) => JSON.stringify(key) + kvSep + encode(v[key], level + 1)).join(sep) + "}";
      } finally {
        seen.delete(v);
      }
    }
    throw new TypeError(`unsupported type: ${typeof v}`);
  }
  return encode(value, 0);
}

// JSON テキストを Python の json.loads に近い形で読む。
// Python が受け付ける NaN / Infinity / -Infinity リテラル（文字列の外側）を
// 実際の値へ変換する。文字列内部には触れない。
function pyJsonParse(text) {
  let out = "";
  let i = 0;
  let inString = false;
  while (i < text.length) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (ch === "\\") {
        out += text[i + 1] ?? "";
        i += 2;
        continue;
      }
      if (ch === '"') inString = false;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inString = true;
      out += ch;
      i += 1;
      continue;
    }
    if (text.startsWith("-Infinity", i)) {
      out += '"\\u0000-Infinity"';
      i += 9;
      continue;
    }
    if (text.startsWith("Infinity", i)) {
      out += '"\\u0000Infinity"';
      i += 8;
      continue;
    }
    if (text.startsWith("NaN", i)) {
      out += '"\\u0000NaN"';
      i += 3;
      continue;
    }
    out += ch;
    i += 1;
  }
  const parsed = JSON.parse(out);
  return restoreNonFinite(parsed);
}

function restoreNonFinite(value) {
  if (value === "\u0000Infinity") return Infinity;
  if (value === "\u0000-Infinity") return -Infinity;
  if (value === "\u0000NaN") return NaN;
  if (Array.isArray(value)) return value.map(restoreNonFinite);
  if (value !== null && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = restoreNonFinite(item);
    }
    return result;
  }
  return value;
}

function containsNonFinite(value) {
  if (typeof value === "number") return !Number.isFinite(value);
  if (Array.isArray(value)) return value.some(containsNonFinite);
  if (value !== null && typeof value === "object") {
    return Object.values(value).some(containsNonFinite);
  }
  return false;
}

module.exports = { pyRepr, pyFloatStr, pyJsonStringify, pyJsonParse, containsNonFinite };
