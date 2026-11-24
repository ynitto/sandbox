"use strict";

const http = require("node:http");
const { createReadStream } = require("node:fs");
const { realpath, readdir, stat } = require("node:fs/promises");
const { resolve, relative, isAbsolute, extname, sep } = require("node:path");
const { pipeline } = require("node:stream/promises");
const { ArgParser, UsageError } = require("../core/args");
const { integer } = require("../core/command-input");

const name = "serve";
const help = "ディレクトリをローカルの静的 HTTP サーバーで配信する";
const usage = "devkit serve [--port N] [--dir DIR]";
const example = "devkit serve --port 8000 --dir public";

const types = {
  html: "text/html; charset=utf-8", css: "text/css; charset=utf-8",
  js: "text/javascript; charset=utf-8", mjs: "text/javascript; charset=utf-8",
  json: "application/json", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
  gif: "image/gif", svg: "image/svg+xml", webp: "image/webp", ico: "image/x-icon",
  txt: "text/plain; charset=utf-8", md: "text/markdown; charset=utf-8",
  pdf: "application/pdf", zip: "application/zip", mp4: "video/mp4",
};

function escapeHtml(value) {
  const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return value.replace(/[&<>"']/g, (char) => entities[char]);
}

function normalizePath(url) {
  const decoded = decodeURIComponent(url.split("?")[0]);
  if (!decoded.startsWith("/") || decoded.includes("\0")) throw new URIError();
  const parts = [];
  for (const part of decoded.split(/[\/\\]+/)) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
    } else parts.push(part);
  }
  return parts;
}

function isInside(root, candidate) {
  const local = relative(root, candidate);
  return local !== ".." && !local.startsWith(".." + sep) && !isAbsolute(local);
}

async function within(root, candidate) {
  if (!isInside(root, candidate)) return null;
  const canonical = await realpath(candidate);
  return isInside(root, canonical) ? canonical : null;
}

function send(response, status, body, type = "text/plain; charset=utf-8") {
  response.writeHead(status, { "Content-Type": type, "Content-Length": Buffer.byteLength(body) });
  response.end(body);
}

async function listing(response, directory, parts) {
  const prefix = "/" + (parts.length ? parts.map(encodeURIComponent).join("/") + "/" : "");
  const entries = await readdir(directory, { withFileTypes: true });
  const links = entries.map((entry) => {
    const suffix = entry.isDirectory() ? "/" : "";
    return `<li><a href="${prefix}${encodeURIComponent(entry.name)}${suffix}">${escapeHtml(entry.name)}${suffix}</a></li>`;
  });
  send(response, 200, `<!doctype html><meta charset="utf-8"><title>Index</title><ul>${links.join("")}</ul>\n`, types.html);
}

async function handle(request, response, root) {
  if (!["GET", "HEAD"].includes(request.method)) {
    response.setHeader("Allow", "GET, HEAD");
    return send(response, 405, "Method Not Allowed\n");
  }
  try {
    const parts = normalizePath(request.url);
    if (!parts) return send(response, 403, "Forbidden\n");
    let candidate = resolve(root, parts.join(sep));
    let file = await within(root, candidate);
    if (!file) return send(response, 403, "Forbidden\n");
    let info = await stat(file);
    if (info.isDirectory()) {
      candidate = resolve(file, "index.html");
      try {
        const index = await within(root, candidate);
        if (!index) return send(response, 403, "Forbidden\n");
        const indexInfo = await stat(index);
        if (!indexInfo.isFile()) return await listing(response, file, parts);
        file = index;
        info = indexInfo;
      } catch (error) {
        if (error.code === "ENOENT") return await listing(response, file, parts);
        throw error;
      }
    }
    if (!info.isFile()) return send(response, 404, "Not Found\n");
    const extension = extname(candidate).slice(1).toLowerCase();
    response.writeHead(200, {
      "Content-Type": Object.hasOwn(types, extension) ? types[extension] : "application/octet-stream",
      "Content-Length": info.size,
    });
    if (request.method === "HEAD") response.end();
    else await pipeline(createReadStream(file), response);
  } catch (error) {
    if (response.headersSent || response.destroyed) return response.destroy();
    if (error instanceof URIError) return send(response, 400, "Bad Request\n");
    if (["ENOENT", "ENOTDIR"].includes(error.code)) return send(response, 404, "Not Found\n");
    if (["EACCES", "EPERM"].includes(error.code)) return send(response, 403, "Forbidden\n");
    send(response, 500, "Internal Server Error\n");
  }
}

async function run(argv, io) {
  const parser = new ArgParser({ prog: `devkit ${name}`, usage, description: help });
  parser.addOption("--port", { type: integer(0, 65535), def: 8000 });
  parser.addOption("--dir", { def: "." });
  const { port, dir } = parser.parse(argv);
  const displayRoot = resolve(io.cwd, dir);
  let root;
  try {
    root = await realpath(displayRoot);
    if (!(await stat(root)).isDirectory()) throw new Error("ディレクトリを指定してください");
  } catch (error) {
    throw new UsageError(`${displayRoot}: ${error.message}`);
  }
  return new Promise((done, reject) => {
    const server = http.createServer((request, response) => handle(request, response, root));
    server.once("error", (error) => reject(new UsageError(`サーバーを起動できません: ${error.message}`)));
    server.once("close", () => done(0));
    server.listen(port, "localhost", () => {
      io.out(`http://localhost:${server.address().port}/ で配信中: ${displayRoot}\n`);
    });
  });
}

module.exports = { name, help, usage, example, run };
