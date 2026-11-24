"use strict";

const { randomUUID } = require("node:crypto");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "uuid";
const help = "UUID v4 を生成し、指定された UUID の形式を検証する";
const usage = "devkit uuid [--count N] [--upper] [--json] [uuid]";
const example = "devkit uuid --count 3";

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit uuid", usage, description: help });
  parser.addPositional("uuid", { nargs: "?", help: "検証する UUID" });
  parser.addOption("--count", { metavar: "N", type: integer(1), help: "生成する個数（既定値: 1）" });
  parser.addFlag("--upper", { help: "生成する UUID を大文字にする" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { uuid, count, upper, json } = parser.parse(argv);

  if (uuid !== undefined) {
    if (count !== undefined) throw new UsageError("--count と UUID は併用できません");
    const parts = /^([\da-f]{8})-([\da-f]{4})-([\da-f]{4})-([\da-f]{4})-([\da-f]{12})$/i.exec(uuid);
    if (!parts || parts[0] !== uuid) throw new UsageError("UUID は 8-4-4-4-12 桁の16進形式で指定してください");
    const version = parseInt(parts[3][0], 16);
    const bits = parseInt(parts[4][0], 16);
    let variant = "予約済み";
    if (bits < 8) variant = "NCS";
    else if (bits < 12) variant = "RFC 4122";
    else if (bits < 14) variant = "Microsoft";

    io.out(json
      ? JSON.stringify({ uuid, valid: true, version, variant }) + "\n"
      : `UUID: ${uuid}\n形式: 正常\nバージョン: ${version}\nバリアント: ${variant}\n`);
    return 0;
  }

  const uuids = [];
  for (let i = 0; i < (count ?? 1); i += 1) {
    const uuid = randomUUID();
    const value = upper ? uuid.toUpperCase() : uuid;
    if (json) uuids.push(value);
    else io.out(value + "\n");
  }
  if (json) io.out(JSON.stringify(uuids) + "\n");
  return 0;
}

module.exports = { name, help, usage, example, run };
