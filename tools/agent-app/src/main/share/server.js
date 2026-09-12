'use strict';

// 参加者どうしの HTTP（Node 標準 http。依存なし）。各 agent-app が LAN 向けに 1 ポート開く。
//
//   POST /hello                         仲間の発見（自分の宣言を渡し、相手の宣言と仲間の一覧を受け取る）
//   POST /notify                        投函の通知（NEW）
//   GET  /node                          自分の宣言（HELLO と同じ）
//   GET  /requests                      自分が投函した open / working の依頼（本文は含めない）
//   POST /requests/<id>/claim           参加者が拾う。依頼者が先着 1 件だけ 200（本文つき）、以後 409
//   POST /requests/<id>/heartbeat       執行者が 30 秒ごと。途絶えたら依頼者が列へ戻す
//   POST /requests/<id>/result          答え。依頼者が会話に保存する
//   POST /requests/<id>/cancel          依頼者 → 執行者。CLI を止める
//   POST /requests/<id>/message         人と人のひとこと（両向き。CLI には入らない）
//   GET  /requests/<id>/attachments/<n> 添付（依頼者が持つ）
//
// 合言葉の sha256 を x-share-key で照合する。違えば 401（LAN の他の機器を弾くだけの門）。

const http = require('http');
const fs = require('fs');
const crypto = require('crypto');

const MAX_BODY = 4 * 1024 * 1024;
const TIMEOUT_MS = 5000;

function sameKey(a, b) {
  const x = Buffer.from(String(a || ''));
  const y = Buffer.from(String(b || ''));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}

function readBody(req, limit = MAX_BODY) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) { reject(new Error('本文が大きすぎます')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      if (!text) { resolve({}); return; }
      try { resolve(JSON.parse(text)); } catch { reject(new Error('JSON ではありません')); }
    });
    req.on('error', reject);
  });
}

function json(res, status, body) {
  const text = JSON.stringify(body == null ? {} : body);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(text) });
  res.end(text);
}

// handlers: { hello(body, remote), notify(body, remote), node(), requests(),
//             claim(id, body, remote), heartbeat(id, body, remote), result(id, body, remote), cancel(id, body, remote),
//             message(id, body, remote), attachment(id, name) → path }
// 各 handler は { status, body } を返す（throw は 500）。remote は相手のアドレス（::ffff: を剥いだもの）。
function remoteOf(req) {
  return String((req.socket && req.socket.remoteAddress) || '').replace(/^::ffff:/, '');
}

function createServer({ key, handlers }) {
  const server = http.createServer(async (req, res) => {
    try {
      if (!sameKey(req.headers['x-share-key'], key)) { json(res, 401, { error: '合言葉が違います' }); return; }
      const url = new URL(req.url, 'http://x');
      const parts = url.pathname.split('/').filter(Boolean);
      if (req.method === 'POST' && parts.length === 1 && (parts[0] === 'hello' || parts[0] === 'notify')) {
        const body = await readBody(req);
        const r = await handlers[parts[0]](body, remoteOf(req));
        json(res, r.status || 200, r.body);
        return;
      }
      if (req.method === 'GET' && parts[0] === 'node' && parts.length === 1) { const r = await handlers.node(); json(res, r.status || 200, r.body); return; }
      if (req.method === 'GET' && parts[0] === 'requests' && parts.length === 1) { const r = await handlers.requests(); json(res, r.status || 200, r.body); return; }
      if (parts[0] === 'requests' && parts.length === 4 && parts[2] === 'attachments' && req.method === 'GET') {
        const file = await handlers.attachment(decodeURIComponent(parts[1]), decodeURIComponent(parts[3]));
        if (!file) { json(res, 404, { error: '添付がありません' }); return; }
        const stat = fs.statSync(file);
        res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': stat.size });
        fs.createReadStream(file).pipe(res);
        return;
      }
      if (parts[0] === 'requests' && parts.length === 3 && req.method === 'POST') {
        const id = decodeURIComponent(parts[1]);
        const action = parts[2];
        if (!['claim', 'heartbeat', 'result', 'cancel', 'message'].includes(action)) { json(res, 404, { error: '不明な操作' }); return; }
        const body = await readBody(req);
        const r = await handlers[action](id, body, remoteOf(req));
        json(res, r.status || 200, r.body);
        return;
      }
      json(res, 404, { error: 'not found' });
    } catch (err) {
      json(res, 500, { error: (err && err.message) || String(err) });
    }
  });
  server.keepAliveTimeout = 5000;
  return {
    server,
    listen(port = 0, host = '0.0.0.0') {
      return new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, host, () => resolve(server.address().port));
      });
    },
    close() { return new Promise((resolve) => server.close(() => resolve())); },
  };
}

// 相手の agent-app を呼ぶ。返り値 { status, body }。つながらなければ throw。
function call(peer, method, pathname, { key, body = null, timeoutMs = TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    const data = body == null ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({
      host: peer.address, port: peer.port, method, path: pathname, timeout: timeoutMs,
      headers: { 'x-share-key': key, ...(data ? { 'content-type': 'application/json', 'content-length': data.length } : {}) },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let parsed = {};
        try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = { raw: text }; }
        resolve({ status: res.statusCode, body: parsed });
      });
    });
    req.on('timeout', () => { req.destroy(new Error('応答がありません')); });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// 添付を相手から写す（ストリーム）。
function download(peer, pathname, target, { key, timeoutMs = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: peer.address, port: peer.port, method: 'GET', path: pathname, timeout: timeoutMs, headers: { 'x-share-key': key } }, (res) => {
      if (res.statusCode !== 200) { res.resume(); reject(new Error(`添付を取れません（${res.statusCode}）`)); return; }
      const out = fs.createWriteStream(target);
      res.pipe(out);
      out.on('finish', () => resolve(target));
      out.on('error', reject);
    });
    req.on('timeout', () => { req.destroy(new Error('応答がありません')); });
    req.on('error', reject);
    req.end();
  });
}

module.exports = { createServer, call, download, sameKey, remoteOf, MAX_BODY };
