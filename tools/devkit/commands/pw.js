"use strict";

const { randomInt } = require("node:crypto");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "pw";
const help = "安全なランダムパスワードを生成する";
const usage = "devkit pw [--length N] [--count N] [--no-symbols] [--no-ambiguous]";
const example = "devkit pw --length 24";

function pick(characters) {
  return characters[randomInt(characters.length)];
}

function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit pw", usage, description: help });
  parser.addOption("--length", { metavar: "N", def: 16, type: integer(1), help: "文字数（既定値: 16）" });
  parser.addOption("--count", { metavar: "N", def: 1, type: integer(1), help: "生成する個数（既定値: 1）" });
  parser.addFlag("--no-symbols", { help: "記号を除く" });
  parser.addFlag("--no-ambiguous", { help: "見分けにくい文字を除く" });
  const args = parser.parse(argv);

  let sets = ["abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789"];
  if (!args.no_symbols) sets.push("!@#$%^&*()-_=+[]{}:,.?");
  if (args.no_ambiguous) sets = sets.map((set) => set.replace(/[0O1lI]/g, ""));
  if (args.length < sets.length) {
    throw new UsageError(`--length は有効な文字種の数（${sets.length}）以上にしてください`);
  }
  const alphabet = sets.join("");
  for (let i = 0; i < args.count; i += 1) {
    const password = sets.map(pick);
    while (password.length < args.length) password.push(pick(alphabet));
    for (let j = password.length - 1; j > 0; j -= 1) {
      const k = randomInt(j + 1);
      [password[j], password[k]] = [password[k], password[j]];
    }
    io.out(password.join("") + "\n");
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
