"use strict";

const { randomBytes } = require("node:crypto");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "ulid";
const help = "ULID を生成し、指定された ULID の生成時刻を表示する";
const usage = "devkit ulid [--count N] [--time 時刻] [--json] | devkit ulid <ULID> [--json]";
const example = "devkit ulid --count 3";
const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

function generate(timestamp) {
  let value = (BigInt(timestamp) << 80n) | BigInt("0x" + randomBytes(10).toString("hex"));
  let ulid = "";
  for (let i = 0; i < 26; i++) {
    ulid = ALPHABET[Number(value & 31n)] + ulid;
    value >>= 5n;
  }
  return ulid;
}

function parseTime(time) {
  if (time === undefined) return Date.now();
  let timestamp = NaN;
  const iso = /^((\d{4}|[+-]\d{6})-(\d{2})-(\d{2}))(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/i.exec(time);
  if (/^\+?\d+$/.test(time) && time.trim() === time) timestamp = Number(time);
  else if (iso && iso[0] === time) {
    const utc = /t/i.test(time) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(time) ? time + "Z" : time;
    timestamp = Date.parse(utc);
    const [, date, ...parts] = iso;
    const [year, month, day] = parts.map(Number);
    const calendar = new Date(date);
    if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() + 1 !== month || calendar.getUTCDate() !== day) {
      timestamp = NaN;
    }
  }
  if (!Number.isInteger(timestamp) || timestamp < 0 || timestamp >= 2 ** 48) {
    throw new UsageError("時刻は ULID の範囲内の ISO 8601 またはミリ秒で指定してください");
  }
  return timestamp;
}

function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addPositional("ulid", { nargs: "?" });
  parser.addOption("--count", { type: integer(1, 10000) });
  parser.addOption("--time");
  parser.addFlag("--json");
  const { ulid, count, time, json } = parser.parse(argv);
  if (ulid !== undefined) {
    if (count !== undefined || time !== undefined) throw new UsageError("ULID と --count/--time は併用できません");
    if (ulid.length !== 26 || !/^[0-7][0-9A-HJKMNP-TV-Z]{25}$/i.test(ulid)) throw new UsageError("ULID の形式が不正です");
    let timestamp = 0;
    for (const char of ulid.slice(0, 10).toUpperCase()) timestamp = timestamp * 32 + ALPHABET.indexOf(char);
    const iso = new Date(timestamp).toISOString();
    io.out((json ? JSON.stringify({ ulid, timestamp, iso }) : iso) + "\n");
    return 0;
  }
  const timestamp = parseTime(time);
  const results = [];
  for (let i = 0; i < (count ?? 1); i++) {
    const value = generate(timestamp);
    if (json) results.push(value);
    else io.out(value + "\n");
  }
  if (json) io.out(JSON.stringify(results) + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
