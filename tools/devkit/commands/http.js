"use strict";

const http = require("node:http");
const https = require("node:https");
const { performance } = require("node:perf_hooks");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "http";
const help = "HTTP 応答のステータスとヘッダを確認する";
const usage = "devkit http [--method METHOD] [--timeout MS] [--no-follow] [--json] <url>";
const example = "devkit http https://example.com/";

function parseUrl(value, base) {
  let url;
  try {
    url = new URL(value, base);
  } catch {
    throw new UsageError(`URL が不正です: ${value}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new UsageError(`URL のスキームは http または https を指定してください: ${value}`);
  }
  return url;
}

async function request(url, method, timeout) {
  let timer;
  try {
    return await new Promise((resolve, reject) => {
      const transport = url.protocol === "https:" ? https : http;
      const req = transport.request(url, { method }, (response) => {
        response.once("error", reject);
        response.once("end", () => resolve(response));
        response.resume();
      });
      req.once("error", reject);
      timer = setTimeout(() => {
        reject(new Error(`${timeout} ms でタイムアウトしました`));
        req.destroy();
      }, timeout);
      req.end();
    });
  } finally {
    clearTimeout(timer);
  }
}

async function run(argv, io) {
  const parser = new ArgParser({ prog: "devkit http", usage, description: help });
  parser.addPositional("url", { help: "HTTP または HTTPS の URL" });
  parser.addOption("--method", { def: "GET", help: "HTTP メソッド（既定値: GET）" });
  parser.addOption("--timeout", { metavar: "MS", type: integer(1, 2147483647), def: 10000, help: "各リクエストの制限時間（ミリ秒）" });
  parser.addFlag("--no-follow", { help: "リダイレクトをたどらない" });
  parser.addFlag("--json", { help: "JSON 形式で出力する" });
  const args = parser.parse(argv);
  let url = parseUrl(args.url);
  let method = args.method;
  const redirects = [];
  const start = performance.now();
  try {
    while (true) {
      const response = await request(url, method, args.timeout);
      const { statusCode: status, statusMessage: statusText, headers, rawHeaders } = response;
      const locationHeader = Array.isArray(headers.location) ? headers.location[0] : headers.location;
      if (!args.no_follow && status >= 300 && status < 400 && locationHeader) {
        if (redirects.length === 10) throw new Error("リダイレクトが上限の10回を超えました");
        const location = parseUrl(locationHeader, url);
        redirects.push({ status, location: location.href });
        if (!args.json) io.out(`${status} ${url.href} -> ${location.href}\n`);
        if ([301, 302, 303].includes(status)) method = "GET";
        url = location;
        continue;
      }

      const timeMs = Math.round(performance.now() - start);
      if (args.json) {
        io.out(JSON.stringify({ url: url.href, status, statusText, timeMs, redirects, headers }) + "\n");
      } else {
        io.out(`${status} ${statusText}\ntime: ${timeMs} ms\n`);
        for (let i = 0; i < rawHeaders.length; i += 2) {
          io.out(`${rawHeaders[i]}: ${rawHeaders[i + 1]}\n`);
        }
      }
      return 0;
    }
  } catch (error) {
    if (error instanceof UsageError) throw error;
    io.err(`HTTP リクエストに失敗しました (${url.href}): ${error.message}\n`);
    return 1;
  }
}

module.exports = { name, help, usage, example, run };
