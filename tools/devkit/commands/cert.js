"use strict";

const { isIP } = require("node:net");
const tls = require("node:tls");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "cert";
const help = "TLS 証明書の情報と有効期限を確認する";
const usage = "devkit cert [--port PORT] [--warn-days DAYS] [--json] <host>";
const example = "devkit cert example.com --warn-days 30";

function readCertificate(host, port) {
  return new Promise((resolve, reject) => {
    const socket = tls.connect({
      host, port, servername: isIP(host) ? undefined : host, rejectUnauthorized: false,
    });
    socket.once("error", reject);
    socket.once("close", () => reject(new Error("証明書を取得する前に接続が閉じました")));
    socket.setTimeout(10000, () => socket.destroy(new Error("TLS 接続がタイムアウトしました")));
    socket.once("secureConnect", () => {
      resolve(socket.getPeerCertificate());
      socket.destroy();
    });
  });
}

async function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit cert", usage, description: help });
  parser.addPositional("host", { help: "接続先ホスト" });
  parser.addOption("--port", { type: integer(1, 65535), def: 443, help: "接続先ポート（既定値: 443）" });
  parser.addOption("--warn-days", { metavar: "DAYS", type: integer(0), def: 30, help: "有効期限の警告しきい値（既定値: 30 日）" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const args = parser.parse(argv);
  if (!args.host.trim()) throw new UsageError("ホストを指定してください");

  let result;
  try {
    const { subject, issuer, valid_from, valid_to, subjectaltname } = await readCertificate(args.host, args.port);
    const expiry = Date.parse(valid_to);
    if (!Number.isFinite(expiry)) throw new Error("証明書の有効期限を取得できません");
    const days_remaining = Math.floor((expiry - Date.now()) / 86400000);
    result = { subject, issuer, valid_from, valid_to, days_remaining, subjectaltname: subjectaltname ?? null };
  } catch (error) {
    io.err(`TLS 証明書を取得できません (${args.host}:${args.port}): ${error.message}\n`);
    return 1;
  }

  if (args.json) io.out(JSON.stringify(result) + "\n");
  else {
    for (const [key, value] of Object.entries(result)) {
      const text = value === null ? "なし" : typeof value === "object" ? JSON.stringify(value) : value;
      io.out(`${key}: ${text}\n`);
    }
  }
  if (result.days_remaining < args.warn_days) {
    io.err(result.days_remaining < 0
      ? "警告: 証明書の有効期限が切れています\n"
      : `警告: 証明書の残り日数が ${args.warn_days} 日未満です\n`);
    return 1;
  }
  return 0;
}

module.exports = { name, help, usage, example, run };
