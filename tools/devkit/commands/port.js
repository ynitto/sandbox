"use strict";

const net = require("node:net");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "port";
const help = "TCP ポートに接続できるか確認する";
const usage = "devkit port --port PORT[,PORT...] [--timeout MS] [--json] <host>";
const example = "devkit port example.com --port 80,443";

function probe(host, port, timeout) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const finish = (error) => {
      socket.destroy();
      resolve(error === undefined ? { host, port, open: true } : { host, port, open: false, error });
    };
    socket.once("connect", () => finish());
    socket.once("error", (error) => finish(error.message));
    socket.setTimeout(timeout, () => finish(`${timeout} ms でタイムアウトしました`));
    socket.connect({ host, port });
  });
}

async function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit port", usage, description: help });
  parser.addPositional("host", { help: "接続先ホスト" });
  parser.addOption("--port", {
    required: true,
    type: (raw) => raw.split(",").map(integer(1, 65535)),
    help: "接続先ポート（カンマ区切りで複数指定可）",
  });
  parser.addOption("--timeout", { metavar: "MS", type: integer(1, 2147483647), def: 3000, help: "接続の制限時間（ミリ秒）" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const { host, port: ports, timeout, json } = parser.parse(argv);
  if (!host.trim()) throw new UsageError("ホストを指定してください");

  const results = await Promise.all(ports.map((port) => probe(host, port, timeout)));
  if (json) io.out(JSON.stringify(results) + "\n");
  else {
    for (const { port, open, error } of results) {
      io.out(`${host}:${port} ${open ? "open" : `closed (${error})`}\n`);
    }
  }
  return results.every(({ open }) => open) ? 0 : 1;
}

module.exports = { name, help, usage, example, run };
