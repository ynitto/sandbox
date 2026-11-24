"use strict";

const { UsageError } = require("./args");

const numberPattern = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i;
const ambiguous = /^(?:null|~|true|false|yes|no|on|off|\.inf|\.nan|[+-]?0[xob][\da-f]+|\d{4}-\d{2}-\d{2}.*)$/i;

function fail(line, message) {
  throw new UsageError(`${line} 行目: ${message}`);
}

function unsupported(line) {
  fail(line, "対応していない記法です");
}

function quotedEnd(text, start, line) {
  const quote = text[start];
  for (let i = start + 1; i < text.length; i++) {
    if (quote === '"' && text[i] === "\\") i++;
    else if (text[i] === quote) {
      if (quote === "'" && text[i + 1] === "'") i++;
      else return i + 1;
    }
  }
  fail(line, "引用符が閉じていません");
}

function scalarStart(text, i) {
  const prefix = text.slice(0, i).trimEnd();
  return !prefix || prefix === "-" || /[\[{},:]$/.test(prefix) || /(?:^|\s)-(?:\s+-)*$/.test(prefix);
}

function stripComment(text, line) {
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "#" && (i === 0 || /\s/.test(text[i - 1]))) return text.slice(0, i).trimEnd();
    if ((char === "'" || char === '"') && scalarStart(text, i)) i = quotedEnd(text, i, line) - 1;
  }
  return text.trimEnd();
}

function blockColon(text, line) {
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if ((char === "'" || char === '"') && scalarStart(text, i)) i = quotedEnd(text, i, line) - 1;
    else if (char === "[" || char === "{") depth++;
    else if (char === "]" || char === "}") depth--;
    else if (!depth && char === ":" && (i === text.length - 1 || /\s/.test(text[i + 1]))) return i;
  }
  return -1;
}

function plain(text, line) {
  if (/^(?:[&*!|>]|---(?:\s|$)|\.\.\.(?:\s|$)|%)/.test(text)) unsupported(line);
  if (/^(?:null|~)$/i.test(text)) return null;
  if (/^(?:true|false)$/i.test(text)) return text.toLowerCase() === "true";
  if (numberPattern.test(text)) {
    // 先頭ゼロの整数と、倍精度では正確に表せない整数は、文字列のまま保持する。
    if (/^[+-]?0\d/.test(text)) return text;
    const number = Number(text);
    if (!Number.isFinite(number)) fail(line, "数値が範囲外です");
    if (Number.isInteger(number) && !Number.isSafeInteger(number)) return text;
    return number;
  }
  return text;
}

function unquote(text, line) {
  if (text[0] === "'") return text.slice(1, -1).replace(/''/g, "'");
  try {
    return JSON.parse(text);
  } catch {
    fail(line, "二重引用符内のエスケープが不正です");
  }
}

function keyOf(text, line) {
  if (!text) fail(line, "キーを指定してください");
  if (/^['"]/.test(text)) {
    if (quotedEnd(text, 0, line) !== text.length) fail(line, "キーの引用符の後に余分な文字があります");
    return unquote(text, line);
  }
  if (/^(?:[&*!|>]|---(?:\s|$))/.test(text)) unsupported(line);
  if (/^[\[{}\]?]/.test(text)) fail(line, "キーの形式が不正です");
  return text;
}

function inline(text, line) {
  if (text[0] !== "[" && text[0] !== "{") {
    if (/^['"]/.test(text)) {
      if (quotedEnd(text, 0, line) !== text.length) fail(line, "引用符の後に余分な文字があります");
      return unquote(text, line);
    }
    return plain(text, line);
  }
  let cursor = 0;
  const skip = () => { while (/\s/.test(text[cursor] ?? "")) cursor++; };
  function token(stops) {
    skip();
    const start = cursor;
    if (/^['"]/.test(text[cursor] ?? "")) cursor = quotedEnd(text, cursor, line);
    else while (cursor < text.length && !stops.includes(text[cursor])) cursor++;
    return text.slice(start, cursor).trimEnd();
  }
  function value(depth) {
    skip();
    if (text[cursor] === "[" || text[cursor] === "{") return collection(depth + 1);
    const raw = token(",]}");
    if (!raw) fail(line, "フロー記法に値が必要です");
    return inline(raw, line);
  }
  function collection(depth) {
    if (depth > 100) fail(line, "フロー記法のネストが深すぎます");
    const map = text[cursor++] === "{";
    const closing = map ? "}" : "]";
    const result = map ? Object.create(null) : [];
    skip();
    while (text[cursor] !== closing) {
      if (map) {
        const key = keyOf(token(":,]}"), line);
        skip();
        if (text[cursor++] !== ":") fail(line, "フローマップに : が必要です");
        result[key] = value(depth);
      } else result.push(value(depth));
      skip();
      if (text[cursor] === closing) break;
      if (text[cursor++] !== ",") fail(line, `フロー記法に , または ${closing} が必要です`);
      skip();
    }
    cursor++;
    return result;
  }
  const result = collection(0);
  skip();
  if (cursor !== text.length) fail(line, "フロー記法の後に余分な文字があります");
  return result;
}

function parseYaml(input) {
  const lines = [];
  for (const [index, raw] of input.replace(/^\ufeff/, "").split(/\r\n|\r|\n/).entries()) {
    const line = index + 1;
    if (/^ *\t/.test(raw)) fail(line, "インデントにはスペースを使ってください");
    const indent = raw.match(/^ */)[0].length;
    const text = stripComment(raw.slice(indent), line);
    if (/^(?:---|\.\.\.)(?:\s|$)|^%/.test(text)) unsupported(line);
    if (text) lines.push({ line, indent, text });
  }
  let cursor = 0;
  const listItem = (text) => /^-(?:\s|$)/.test(text);

  function block(indent, first, depth = 0) {
    const initial = first ?? lines[cursor++];
    if (depth > 100) fail(initial.line, "ネストが深すぎます");
    if (listItem(initial.text)) {
      const result = [];
      let entry = initial;
      do {
        const prefix = entry.text.match(/^-\s*/)[0];
        const text = entry.text.slice(prefix.length);
        if (text && (listItem(text) || blockColon(text, entry.line) >= 0)) {
          result.push(block(indent + prefix.length, { ...entry, text }, depth + 1));
        } else if (text) result.push(inline(text, entry.line));
        else result.push(nested(indent, false, depth));
        entry = lines[cursor];
        if (entry?.indent !== indent || !listItem(entry.text)) break;
        cursor++;
      } while (true);
      return result;
    }
    if (blockColon(initial.text, initial.line) < 0) return inline(initial.text, initial.line);
    const result = Object.create(null);
    let entry = initial;
    do {
      const colon = blockColon(entry.text, entry.line);
      if (colon < 0) fail(entry.line, "マップに : が必要です");
      const key = keyOf(entry.text.slice(0, colon).trimEnd(), entry.line);
      const text = entry.text.slice(colon + 1).trimStart();
      if (text && blockColon(text, entry.line) >= 0) fail(entry.line, "値に含む : は引用符で囲んでください");
      result[key] = text ? inline(text, entry.line) : nested(indent, true, depth);
      entry = lines[cursor];
      if (entry?.indent !== indent || listItem(entry.text)) break;
      cursor++;
    } while (true);
    return result;
  }

  function nested(indent, allowIndentless = false, depth = 0) {
    const next = lines[cursor];
    if (next && (next.indent > indent || (allowIndentless && next.indent === indent && listItem(next.text)))) {
      return block(next.indent, undefined, depth + 1);
    }
    return null;
  }

  if (!lines.length) return null;
  const result = block(lines[0].indent);
  if (cursor < lines.length) {
    const { text, line } = lines[cursor];
    if (/^(?:[&*!|>]|---(?:\s|$)|\.\.\.(?:\s|$)|%)/.test(text)) unsupported(line);
    fail(line, "インデントまたは構造が不正です");
  }
  return result;
}

function formatScalar(value) {
  if (typeof value === "number" && !Number.isFinite(value)) throw new UsageError("JSON の数値が範囲外です");
  if (typeof value !== "string") return JSON.stringify(value);
  if (!value || value.trim() !== value || numberPattern.test(value) || ambiguous.test(value) ||
      /^[-?%]/.test(value) || /[:#\[\]{},'"&*!|>@`\\\p{C}\u2028\u2029]/u.test(value)) {
    return JSON.stringify(value);
  }
  return value;
}

function formatYaml(value) {
  const rows = [];
  function render(node, depth, prefix = "") {
    const indent = "  ".repeat(depth);
    const array = Array.isArray(node);
    if (node === null || typeof node !== "object") {
      rows.push(indent + prefix + (prefix ? " " : "") + formatScalar(node));
      return;
    }
    const entries = Object.entries(node);
    if (!entries.length) {
      rows.push(indent + prefix + (prefix ? " " : "") + (array ? "[]" : "{}"));
      return;
    }
    if (prefix) rows.push(indent + prefix);
    for (const [key, child] of entries) {
      render(child, depth + (prefix ? 1 : 0), array ? "-" : formatScalar(key) + ":");
    }
  }
  render(value, 0);
  return rows.join("\n") + "\n";
}

module.exports = { parseYaml, formatYaml };
