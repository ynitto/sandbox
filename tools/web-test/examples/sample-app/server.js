#!/usr/bin/env node
'use strict';
// 動作確認用の小さな Web アプリ（ログイン → 一覧）。`node examples/sample-app/server.js [port]`
const http = require('http');
const fs = require('fs');
const path = require('path');

const ITEMS = [{ id: 1, name: 'りんご', price: 120 }, { id: 2, name: 'みかん', price: 80 }, { id: 3, name: 'ぶどう', price: 450 }];

function createServer() {
  return http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/api/items') {
      res.writeHead(200, { 'content-type': 'application/json' });
      return res.end(JSON.stringify(ITEMS));
    }
    const file = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\/+/, '');
    const p = path.join(__dirname, path.normalize(file));
    if (!p.startsWith(__dirname) || !fs.existsSync(p)) {
      res.writeHead(404);
      return res.end('not found');
    }
    res.writeHead(200, { 'content-type': p.endsWith('.html') ? 'text/html; charset=utf-8' : 'text/plain' });
    fs.createReadStream(p).pipe(res);
  });
}

if (require.main === module) {
  const port = Number(process.argv[2] || 3000);
  createServer().listen(port, () => console.log(`http://localhost:${port}/`));
}

module.exports = { createServer };
