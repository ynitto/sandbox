"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { ArgParser, ArgTypeError } = require("../core/args");
const { pyRepr } = require("../core/py");

const DELIMITERS = ",\t;|";
const BOM = Buffer.from([0xef, 0xbb, 0xbf]);
const INTEGER = /^[+-]?[0-9]+$/;
const DECIMAL = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
const trim = (text) => text.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, "");

class DecodeError extends Error {}

// Input is split on encoded line endings, including CRLF and bare CR.
function* byteLines(buffer, encoding) {
  const utf16 = encoding === "utf-16le" || encoding === "utf-16be";
  const step = utf16 ? 2 : 1;
  const unit = encoding === "utf-16le" ? (i) => buffer.readUInt16LE(i)
    : encoding === "utf-16be" ? (i) => buffer.readUInt16BE(i)
      : (i) => buffer[i];
  let start = 0;
  for (let i = 0; i + step <= buffer.length; i += step) {
    const char = unit(i);
    if (char !== 10 && char !== 13) continue;
    if (char === 13 && i + 2 * step <= buffer.length && unit(i + step) === 10) i += step;
    const end = i + step;
    yield buffer.subarray(start, end);
    start = end;
  }
  if (start < buffer.length) yield buffer.subarray(start);
}

const SHIFT_JIS_OVERRIDES = new Map([
  [0x8160, "〜"], [0x8161, "‖"], [0x817c, "−"],
  [0x8191, "¢"], [0x8192, "£"], [0x81ca, "¬"],
]);

function decodeJapanese(bytes, decoder, cp932) {
  let result = "";
  let start = 0;
  for (let i = 0; i < bytes.length; i++) {
    const byte = bytes[i];
    let replacement;
    const end = i;
    if ((byte >= 0x81 && byte <= 0x9f) || (byte >= 0xe0 && byte <= 0xfc)) {
      if (!cp932 && (byte === 0x87 || byte >= 0xeb)) throw new DecodeError("invalid input byte");
      if (!cp932) replacement = SHIFT_JIS_OVERRIDES.get(byte * 256 + bytes[i + 1]);
      if (i + 1 < bytes.length) i++;
    } else if (byte === 0x1a || byte === 0x1c || byte === 0x7f) {
      // ICU's Shift_JIS decoder permutes these ASCII control bytes.
      replacement = String.fromCharCode(byte);
    } else if (byte === 0x80 || byte === 0xa0 || byte >= 0xfd) {
      if (!cp932) throw new DecodeError("invalid input byte");
      replacement = byte === 0x80 ? "\x80" : String.fromCharCode(0xf8f0 + (byte === 0xa0 ? 0 : byte - 0xfc));
    }
    if (replacement !== undefined) {
      result += decoder.decode(bytes.subarray(start, end)) + replacement;
      start = i + 1;
    }
  }
  return result + decoder.decode(bytes.subarray(start));
}

function decodedLines(buffer, encoding) {
  let label = encoding.toLowerCase();
  if (label === "utf-8-sig") {
    if (buffer.subarray(0, 3).equals(BOM)) buffer = buffer.subarray(3);
    label = "utf-8";
  } else if (label === "utf-16") {
    const bom = buffer.subarray(0, 2).toString("hex");
    if (bom !== "fffe" && bom !== "feff" && buffer.length) {
      throw new DecodeError("UTF-16 stream does not start with BOM");
    }
    label = bom === "feff" ? "utf-16be" : "utf-16le";
    buffer = buffer.subarray(2);
  }
  const cp932 = label === "cp932" || label === "ms932" || label === "windows-31j";
  const decoder = new TextDecoder(cp932 ? "shift_jis" : label, { fatal: true, ignoreBOM: true });
  return (function* () {
    try {
      for (const bytes of byteLines(buffer, decoder.encoding)) {
        const line = decoder.encoding === "shift_jis"
          ? decodeJapanese(bytes, decoder, cp932)
          : decoder.decode(bytes, { stream: decoder.encoding === "iso-2022-jp" && [10, 13].includes(bytes.at(-1)) });
        if (line) yield line;
      }
      const tail = decoder.decode();
      if (tail) yield tail;
    } catch (error) {
      throw new DecodeError(`${encoding}: ${error.message}`);
    }
  })();
}

function* records(lines, delimiter, strict = true) {
  let row = [];
  let field = "";
  let state = "start";
  for (const line of lines) {
    for (const char of line) {
      if (state === "quoted") {
        if (char === '"') state = "closed";
        else field += char;
        continue;
      }
      if (state === "closed" && char === '"') {
        field += char;
        state = "quoted";
        continue;
      }
      if (char === "\n" || char === "\r") {
        if (state !== "start" || row.length) row.push(field);
        break;
      }
      if (state === "start" && char === '"') {
        state = "quoted";
      } else if (char === delimiter) {
        row.push(field);
        field = "";
        state = "start";
      } else {
        if (state === "closed" && strict) throw new Error(`${pyRepr(delimiter)} expected after '"'`);
        field += char;
        state = "plain";
      }
    }
    if (state === "quoted") continue;
    if (!/[\r\n]$/.test(line)) row.push(field);
    yield row;
    row = [];
    field = "";
    state = "start";
  }
  if (state === "quoted") {
    if (strict) throw new Error("unexpected end of data");
    yield [...row, field];
  }
}

function chooseDelimiter(line) {
  let chosen = DELIMITERS[0];
  let width = -1;
  for (const delimiter of DELIMITERS) {
    const count = records(line ? [line] : [], delimiter, false).next().value?.length ?? 0;
    if (count > width) {
      chosen = delimiter;
      width = count;
    }
  }
  return chosen;
}

const ISO_TIME = /^(\d{2})(?:(:?)(\d{2})(?:\2(\d{2}))?)?(?:[.,]\d+)?(?:Z|[+-](\d{2})(?:(:?)(\d{2})(?:\6(\d{2}))?)?(?:[.,]\d+)?)?$/;
const ISO_DATE = /^(\d{4})(?:(-?)(\d{2})\2(\d{2})|(-?)W(\d{2})(?:\5([1-7]))?)(?:([\s\S])(.*))?$/su;

function isIsoTime(value) {
  const match = ISO_TIME.exec(value);
  if (!match) return false;
  const [, hour, , minute = 0, second = 0, offsetHour = 0, , offsetMinute = 0, offsetSecond = 0] = match;
  return hour < 24 && minute < 60 && second < 60 &&
    Number(offsetHour) * 3600 + Number(offsetMinute) * 60 + Number(offsetSecond) < 86400;
}

function isIsoDateTime(value) {
  const match = ISO_DATE.exec(value);
  if (!match) return false;
  const [, yearText, , month, day, , week, weekDay, separator, time] = match;
  const year = Number(yearText);
  if (!year || (separator && !isIsoTime(time))) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  if (week !== undefined) {
    const jan1 = new Date(0);
    jan1.setUTCFullYear(year, 0, 1);
    const weekday = jan1.getUTCDay();
    const maxWeek = weekday === 4 || (weekday === 3 && leap) ? 53 : 52;
    return week >= 1 && week <= maxWeek && !(year === 9999 && week === "52" && weekDay > 5);
  }
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= days[Number(month) - 1];
}

function classify(value) {
  if (/^(?:true|false|yes|no)$/iu.test(value)) return ["boolean"];
  if (INTEGER.test(value)) {
    return value.replace(/^[+-]/, "").length <= 4300 ? ["integer", BigInt(value)] : ["string"];
  }
  if (DECIMAL.test(value)) {
    const number = Number(value);
    if (Number.isFinite(number)) return ["decimal", number];
  }
  if (/[-:]/.test(value) && (isIsoDateTime(value) || isIsoTime(value.replace(/^T/, "")))) {
    return ["datetime"];
  }
  return ["string"];
}

class Column {
  types = new Map();
  values = new Set();
  nonempty = 0;
  min;
  max;

  add(cell) {
    const value = trim(cell);
    if (!value) return;
    this.nonempty++;
    this.values.add(value);
    const [kind, number] = classify(value);
    this.types.set(kind, (this.types.get(kind) ?? 0) + 1);
    if (number === undefined) return;
    if (this.min === undefined || number < this.min) this.min = number;
    if (this.max === undefined || number > this.max) this.max = number;
  }

  summary(name, rowCount) {
    const groups = new Map(this.types);
    groups.set("number", (groups.get("integer") ?? 0) + (groups.get("decimal") ?? 0));
    groups.delete("integer");
    groups.delete("decimal");
    let type = "string";
    let count = 0;
    for (const [kind, total] of groups) {
      if (total > count) [type, count] = [kind, total];
    }
    if (this.nonempty && count * 2 <= this.nonempty) type = "mixed";
    if (type === "number") type = this.types.has("decimal") ? "decimal" : "integer";

    const result = { name, type, empty_count: rowCount - this.nonempty, unique_count: this.values.size };
    if (type === "decimal" || type === "integer") Object.assign(result, { min: this.min, max: this.max });
    return result;
  }
}

function summarize(lines, args, encoding) {
  let delimiter = args.delimiter;
  if (delimiter === undefined) {
    const first = args.no_header && args.limit === 0n ? undefined : lines.next().value;
    delimiter = chooseDelimiter(first);
    if (first !== undefined) {
      const rest = lines;
      lines = (function* () { yield first; yield* rest; })();
    }
  }

  const rows = records(lines, delimiter);
  const headers = args.no_header ? [] : (rows.next().value ?? []);
  const columns = headers.map(() => new Column());
  let expectedWidth = args.no_header ? undefined : headers.length;
  let rowCount = 0;
  let uneven = 0;
  const samples = [];

  while (args.limit === null || rowCount < args.limit) {
    const { value: row, done } = rows.next();
    if (done) break;
    expectedWidth ??= row.length;
    if (row.length !== expectedWidth) uneven++;
    while (columns.length < row.length) columns.push(new Column());
    row.forEach((cell, i) => columns[i].add(cell));
    if (samples.length < args.sample) samples.push(row);
    rowCount++;
  }

  while (headers.length < columns.length) headers.push(`col${headers.length + 1}`);
  for (const row of samples) while (row.length < columns.length) row.push("");
  return {
    encoding, delimiter, row_count: rowCount, column_count: columns.length, headers,
    limit: args.limit, uneven_row_count: uneven,
    columns: columns.map((column, i) => column.summary(headers[i], rowCount)), sample_rows: samples,
  };
}

function inspect(buffer, args) {
  const read = (encoding) => summarize(decodedLines(buffer, encoding), args, encoding);
  if (args.encoding) return read(args.encoding);
  if (buffer.subarray(0, 3).equals(BOM)) return read("utf-8-sig");
  for (const encoding of ["utf-8", "shift_jis", "cp932"]) {
    try {
      return read(encoding);
    } catch (error) {
      if (!(error instanceof DecodeError)) throw error;
      if (encoding === "cp932") {
        throw new Error(`UTF-8、Shift_JIS、cp932 のいずれでもデコードできません (${error.message})`);
      }
    }
  }
}

// East Asian Width W/F and nonzero canonical combining classes from Unicode.
const WIDE = new RegExp(String.raw`[\u{1100}-\u{115f}\u{231a}-\u{231b}\u{2329}-\u{232a}\u{23e9}-\u{23ec}\u{23f0}\u{23f3}
\u{25fd}-\u{25fe}\u{2614}-\u{2615}\u{2648}-\u{2653}\u{267f}\u{2693}\u{26a1}\u{26aa}-\u{26ab}
\u{26bd}-\u{26be}\u{26c4}-\u{26c5}\u{26ce}\u{26d4}\u{26ea}\u{26f2}-\u{26f3}\u{26f5}\u{26fa}
\u{26fd}\u{2705}\u{270a}-\u{270b}\u{2728}\u{274c}\u{274e}\u{2753}-\u{2755}\u{2757}
\u{2795}-\u{2797}\u{27b0}\u{27bf}\u{2b1b}-\u{2b1c}\u{2b50}\u{2b55}\u{2e80}-\u{2e99}
\u{2e9b}-\u{2ef3}\u{2f00}-\u{2fd5}\u{2ff0}-\u{2ffb}\u{3000}-\u{303e}\u{3041}-\u{3096}
\u{3099}-\u{30ff}\u{3105}-\u{312f}\u{3131}-\u{318e}\u{3190}-\u{31e3}\u{31f0}-\u{321e}
\u{3220}-\u{3247}\u{3250}-\u{4dbf}\u{4e00}-\u{a48c}\u{a490}-\u{a4c6}\u{a960}-\u{a97c}
\u{ac00}-\u{d7a3}\u{f900}-\u{fa6d}\u{fa70}-\u{fad9}\u{fe10}-\u{fe19}\u{fe30}-\u{fe52}
\u{fe54}-\u{fe66}\u{fe68}-\u{fe6b}\u{ff01}-\u{ff60}\u{ffe0}-\u{ffe6}\u{16fe0}-\u{16fe4}
\u{16ff0}-\u{16ff1}\u{17000}-\u{187f7}\u{18800}-\u{18cd5}\u{18d00}-\u{18d08}
\u{1aff0}-\u{1aff3}\u{1aff5}-\u{1affb}\u{1affd}-\u{1affe}\u{1b000}-\u{1b122}\u{1b132}
\u{1b150}-\u{1b152}\u{1b155}\u{1b164}-\u{1b167}\u{1b170}-\u{1b2fb}\u{1f004}\u{1f0cf}\u{1f18e}
\u{1f191}-\u{1f19a}\u{1f200}-\u{1f202}\u{1f210}-\u{1f23b}\u{1f240}-\u{1f248}
\u{1f250}-\u{1f251}\u{1f260}-\u{1f265}\u{1f300}-\u{1f320}\u{1f32d}-\u{1f335}
\u{1f337}-\u{1f37c}\u{1f37e}-\u{1f393}\u{1f3a0}-\u{1f3ca}\u{1f3cf}-\u{1f3d3}
\u{1f3e0}-\u{1f3f0}\u{1f3f4}\u{1f3f8}-\u{1f43e}\u{1f440}\u{1f442}-\u{1f4fc}
\u{1f4ff}-\u{1f53d}\u{1f54b}-\u{1f54e}\u{1f550}-\u{1f567}\u{1f57a}\u{1f595}-\u{1f596}
\u{1f5a4}\u{1f5fb}-\u{1f64f}\u{1f680}-\u{1f6c5}\u{1f6cc}\u{1f6d0}-\u{1f6d2}
\u{1f6d5}-\u{1f6d7}\u{1f6dc}-\u{1f6df}\u{1f6eb}-\u{1f6ec}\u{1f6f4}-\u{1f6fc}
\u{1f7e0}-\u{1f7eb}\u{1f7f0}\u{1f90c}-\u{1f93a}\u{1f93c}-\u{1f945}\u{1f947}-\u{1f9ff}
\u{1fa70}-\u{1fa7c}\u{1fa80}-\u{1fa88}\u{1fa90}-\u{1fabd}\u{1fabf}-\u{1fac5}
\u{1face}-\u{1fadb}\u{1fae0}-\u{1fae8}\u{1faf0}-\u{1faf8}\u{20000}-\u{2a6df}
\u{2a700}-\u{2b739}\u{2b740}-\u{2b81d}\u{2b820}-\u{2cea1}\u{2ceb0}-\u{2ebe0}
\u{2f800}-\u{2fa1d}\u{30000}-\u{3134a}\u{31350}-\u{323af}]`.replaceAll("\n", ""), "u");
const COMBINING = new RegExp(String.raw`[\u{300}-\u{34e}\u{350}-\u{36f}\u{483}-\u{487}\u{591}-\u{5bd}\u{5bf}\u{5c1}-\u{5c2}
\u{5c4}-\u{5c5}\u{5c7}\u{610}-\u{61a}\u{64b}-\u{65f}\u{670}\u{6d6}-\u{6dc}\u{6df}-\u{6e4}
\u{6e7}-\u{6e8}\u{6ea}-\u{6ed}\u{711}\u{730}-\u{74a}\u{7eb}-\u{7f3}\u{7fd}\u{816}-\u{819}
\u{81b}-\u{823}\u{825}-\u{827}\u{829}-\u{82d}\u{859}-\u{85b}\u{898}-\u{89f}\u{8ca}-\u{8e1}
\u{8e3}-\u{8ff}\u{93c}\u{94d}\u{951}-\u{954}\u{9bc}\u{9cd}\u{9fe}\u{a3c}\u{a4d}\u{abc}\u{acd}
\u{b3c}\u{b4d}\u{bcd}\u{c3c}\u{c4d}\u{c55}-\u{c56}\u{cbc}\u{ccd}\u{d3b}-\u{d3c}\u{d4d}\u{dca}
\u{e38}-\u{e3a}\u{e48}-\u{e4b}\u{eb8}-\u{eba}\u{ec8}-\u{ecb}\u{f18}-\u{f19}\u{f35}\u{f37}
\u{f39}\u{f71}-\u{f72}\u{f74}\u{f7a}-\u{f7d}\u{f80}\u{f82}-\u{f84}\u{f86}-\u{f87}\u{fc6}
\u{1037}\u{1039}-\u{103a}\u{108d}\u{135d}-\u{135f}\u{1714}-\u{1715}\u{1734}\u{17d2}\u{17dd}
\u{18a9}\u{1939}-\u{193b}\u{1a17}-\u{1a18}\u{1a60}\u{1a75}-\u{1a7c}\u{1a7f}\u{1ab0}-\u{1abd}
\u{1abf}-\u{1ace}\u{1b34}\u{1b44}\u{1b6b}-\u{1b73}\u{1baa}-\u{1bab}\u{1be6}\u{1bf2}-\u{1bf3}
\u{1c37}\u{1cd0}-\u{1cd2}\u{1cd4}-\u{1ce0}\u{1ce2}-\u{1ce8}\u{1ced}\u{1cf4}\u{1cf8}-\u{1cf9}
\u{1dc0}-\u{1dff}\u{20d0}-\u{20dc}\u{20e1}\u{20e5}-\u{20f0}\u{2cef}-\u{2cf1}\u{2d7f}
\u{2de0}-\u{2dff}\u{302a}-\u{302f}\u{3099}-\u{309a}\u{a66f}\u{a674}-\u{a67d}\u{a69e}-\u{a69f}
\u{a6f0}-\u{a6f1}\u{a806}\u{a82c}\u{a8c4}\u{a8e0}-\u{a8f1}\u{a92b}-\u{a92d}\u{a953}\u{a9b3}
\u{a9c0}\u{aab0}\u{aab2}-\u{aab4}\u{aab7}-\u{aab8}\u{aabe}-\u{aabf}\u{aac1}\u{aaf6}\u{abed}
\u{fb1e}\u{fe20}-\u{fe2f}\u{101fd}\u{102e0}\u{10376}-\u{1037a}\u{10a0d}\u{10a0f}
\u{10a38}-\u{10a3a}\u{10a3f}\u{10ae5}-\u{10ae6}\u{10d24}-\u{10d27}\u{10eab}-\u{10eac}
\u{10efd}-\u{10eff}\u{10f46}-\u{10f50}\u{10f82}-\u{10f85}\u{11046}\u{11070}\u{1107f}
\u{110b9}-\u{110ba}\u{11100}-\u{11102}\u{11133}-\u{11134}\u{11173}\u{111c0}\u{111ca}
\u{11235}-\u{11236}\u{112e9}-\u{112ea}\u{1133b}-\u{1133c}\u{1134d}\u{11366}-\u{1136c}
\u{11370}-\u{11374}\u{11442}\u{11446}\u{1145e}\u{114c2}-\u{114c3}\u{115bf}-\u{115c0}\u{1163f}
\u{116b6}-\u{116b7}\u{1172b}\u{11839}-\u{1183a}\u{1193d}-\u{1193e}\u{11943}\u{119e0}\u{11a34}
\u{11a47}\u{11a99}\u{11c3f}\u{11d42}\u{11d44}-\u{11d45}\u{11d97}\u{11f41}-\u{11f42}
\u{16af0}-\u{16af4}\u{16b30}-\u{16b36}\u{16ff0}-\u{16ff1}\u{1bc9e}\u{1d165}-\u{1d169}
\u{1d16d}-\u{1d172}\u{1d17b}-\u{1d182}\u{1d185}-\u{1d18b}\u{1d1aa}-\u{1d1ad}
\u{1d242}-\u{1d244}\u{1e000}-\u{1e006}\u{1e008}-\u{1e018}\u{1e01b}-\u{1e021}
\u{1e023}-\u{1e024}\u{1e026}-\u{1e02a}\u{1e08f}\u{1e130}-\u{1e136}\u{1e2ae}
\u{1e2ec}-\u{1e2ef}\u{1e4ec}-\u{1e4ef}\u{1e8d0}-\u{1e8d6}\u{1e944}-\u{1e94a}]`.replaceAll("\n", ""), "u");

function width(text) {
  let total = 0;
  for (const char of text) {
    if (!COMBINING.test(char)) total += WIDE.test(char) ? 2 : 1;
  }
  return total;
}

function table(rows) {
  const visible = rows.map((row) => row.map((cell) =>
    cell.replace(/[\x00-\x1f\x7f]/g, (char) => pyRepr(char).slice(1, -1))));
  const widths = rows[0].map(() => 0);
  for (const row of visible) {
    row.forEach((cell, i) => { widths[i] = Math.max(widths[i], width(cell)); });
  }
  return visible.map((row) => row.map((cell, i) =>
    cell + " ".repeat(widths[i] - width(cell))).join(" | ").replace(/[\p{White_Space}\x1c-\x1f]+$/u, ""));
}

function floatText(number) {
  if (Object.is(number, -0)) return "-0.0";
  const magnitude = Math.abs(number);
  if (magnitude && (magnitude < 1e-4 || magnitude >= 1e16)) {
    return number.toExponential().replace(/e([+-])(\d)$/, "e$10$2");
  }
  return Number.isInteger(number) ? `${number}.0` : String(number);
}

function display(result) {
  const note = result.limit === null ? "" : `（先頭 ${result.limit} 行時点の概算、総行数は未集計）`;
  const lines = [
    `文字コード: ${result.encoding}`,
    `区切り文字: ${pyRepr(result.delimiter)}`,
    `行数: ${result.row_count}${note}`,
    `列数: ${result.column_count}`,
  ];
  if (result.columns.length) {
    const keys = ["name", "type", "empty_count", "unique_count", "min", "max"];
    const rows = [["列名", "型", "空欄数", "ユニーク数", "最小", "最大"]];
    for (const column of result.columns) {
      rows.push(keys.map((key) => {
        const value = column[key];
        return (key === "min" || key === "max") && typeof value === "number"
          ? floatText(value) : String(value ?? "");
      }));
    }
    lines.push(...table(rows));
  }
  if (result.sample_rows.length && result.headers.length) {
    lines.push("", "サンプル", ...table([result.headers, ...result.sample_rows]));
  }
  return lines.join("\n");
}

function json(value, key) {
  if (Array.isArray(value)) return "[" + value.map((item) => json(item)).join(", ") + "]";
  if (value !== null && typeof value === "object") {
    return "{" + Object.entries(value).map(([name, item]) => `${JSON.stringify(name)}: ${json(item, name)}`).join(", ") + "}";
  }
  if (typeof value === "bigint") return String(value);
  if (typeof value === "number" && (key === "min" || key === "max")) return floatText(value);
  return JSON.stringify(value);
}

function nonnegative(raw) {
  const value = trim(raw);
  if (!/^[+-]?[0-9](?:_?[0-9])*$/.test(value)) throw new ArgTypeError(`invalid nonnegative value: ${pyRepr(raw)}`);
  const number = BigInt(value.replaceAll("_", ""));
  if (number < 0) throw new ArgTypeError("0 以上の整数を指定してください");
  return number;
}

function parseDelimiter(value) {
  if (value === "tab" || value === "\\t") return "\t";
  if ([...value].length !== 1 || value === "\r" || value === "\n") {
    throw new ArgTypeError("改行以外の 1 文字、または tab を指定してください");
  }
  return value;
}

const usage = "devkit csv [--delimiter DELIMITER] [--no-header] [--sample SAMPLE] [--limit LIMIT] [--json] [--encoding ENCODING] file";

function run(argv, io) {
  const parser = new ArgParser({
    prog: "devkit csv", usage, description: "CSV ファイルの概要を表示します。", negativePattern: /^-\d+$/,
  });
  parser.addPositional("file", { help: "CSV ファイルのパス。- は標準入力" });
  parser.addOption(["--delimiter"], { type: parseDelimiter, help: "区切り文字" });
  parser.addFlag(["--no-header"], { help: "すべての行をデータとして扱う" });
  parser.addOption(["--sample"], { type: nonnegative, def: 5n, help: "表示するサンプル行数" });
  parser.addOption(["--limit"], { type: nonnegative, def: null, help: "集計する先頭データ行数" });
  parser.addFlag(["--json"], { help: "JSON 形式で出力" });
  parser.addOption(["--encoding"], { help: "入力の文字コード" });
  const args = parser.parse(argv);

  let result;
  try {
    const buffer = args.file === "-" ? io.readStdin() : fs.readFileSync(path.resolve(io.cwd, args.file));
    result = inspect(buffer, args);
  } catch (error) {
    io.err(`エラー: ${error.message}\n`);
    return 2;
  }

  try {
    if (result.uneven_row_count) {
      io.err(`警告: 列数が揃っていない行: ${result.uneven_row_count} 件\n`);
    }
    const output = Buffer.from((args.json ? json(result) : display(result)) + "\n");
    // Synchronous writes let the command report EPIPE with its own exit code.
    // io.outSync が無いホストでは fd 1 へ直接書く（従来どおり）。
    if (typeof io.outSync === "function") {
      io.outSync(output);
    } else {
      for (let written = 0; written < output.length;) {
        written += fs.writeSync(1, output, written, output.length - written);
      }
    }
  } catch (error) {
    io.err(`エラー: 出力できません (${error.message})\n`);
    return 2;
  }
  return 0;
}

module.exports = { name: "csv", help: "CSV ファイルの概要を表示する", usage, run };
