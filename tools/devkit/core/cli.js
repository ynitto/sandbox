"use strict";

// devkit のディスパッチャ。
// commands/ ディレクトリ内のモジュールを自動発見して登録する。
// コマンドモジュールの規約:
//   module.exports = {
//     name: "jwt",            // サブコマンド名
//     help: "JWT の中身を表示する",  // 一覧用の1行説明
//     usage: "devkit jwt [--file FILE] [--tz TZ] [--secret SECRET] [token]",
//     run(argv, io) { ... return 終了コード; }  // async でもよい
//   }

const fs = require("fs");
const path = require("path");
const { UsageError, HelpDisplayed } = require("./args");

const COMMANDS_DIR = path.join(__dirname, "..", "commands");

function discoverCommands() {
  const commands = new Map();
  const files = fs
    .readdirSync(COMMANDS_DIR)
    .filter((file) => file.endsWith(".js") && !file.startsWith("_"))
    .sort();
  for (const file of files) {
    const mod = require(path.join(COMMANDS_DIR, file));
    if (!mod || typeof mod.name !== "string" || typeof mod.run !== "function") {
      throw new Error(`commands/${file}: name と run(argv, io) を export してください`);
    }
    if (commands.has(mod.name)) {
      throw new Error(`commands/${file}: コマンド名 ${mod.name} が重複しています`);
    }
    commands.set(mod.name, mod);
  }
  return commands;
}

function topLevelHelp(commands) {
  const lines = [
    "usage: devkit <command> [options]",
    "",
    "IT 系の小物 CLI をまとめたツールキット。外部依存なしで動作します。",
    "",
    "commands:",
  ];
  const width = Math.max(...[...commands.keys()].map((name) => name.length));
  for (const [name, mod] of [...commands.entries()].sort()) {
    lines.push(`  ${name.padEnd(width)}  ${mod.help || ""}`);
  }
  lines.push(
    "",
    "各コマンドの使い方は devkit <command> --help を見てください。"
  );
  return lines.join("\n") + "\n";
}

const TOP_USAGE = "devkit <command> [options]";

async function main(argv, io, commands = discoverCommands()) {
  if (argv.length === 0) {
    io.err(`usage: ${TOP_USAGE}\ndevkit: error: コマンドを指定してください\n`);
    return 2;
  }
  const [head, ...rest] = argv;
  if (head === "-h" || head === "--help") {
    io.out(topLevelHelp(commands));
    return 0;
  }
  if (head === "--version") {
    const pkg = require("../package.json");
    io.out(`devkit ${pkg.version}\n`);
    return 0;
  }
  const command = commands.get(head);
  if (!command) {
    io.err(
      `usage: ${TOP_USAGE}\ndevkit: error: invalid choice: '${head}' ` +
        `(choose from ${[...commands.keys()].sort().join(", ")})\n`
    );
    return 2;
  }
  try {
    const code = await command.run(rest, io);
    return typeof code === "number" ? code : 0;
  } catch (error) {
    if (error instanceof HelpDisplayed) {
      io.out(error.text);
      return 0;
    }
    if (error instanceof UsageError) {
      io.err(`usage: ${command.usage}\ndevkit ${command.name}: error: ${error.message}\n`);
      return 2;
    }
    throw error;
  }
}

module.exports = { main, discoverCommands, topLevelHelp };
