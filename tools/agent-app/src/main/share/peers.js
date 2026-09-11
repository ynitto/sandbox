'use strict';

// 仲間の発見。依存なし（Node 標準の http と dgram）。3 段で、どれか 1 つ通れば全員につながる。
//
//   1. 静的な仲間   設定の「仲間の PC」（host か host:port）へ 30 秒ごとに POST /hello（TCP）
//   2. ゴシップ     /hello の返事に「相手が知っている仲間」が載る。1 台知れば全員に広がる
//   3. UDP          ブロードキャストの HELLO。通れば設定なしで見つかる。通らなくても黙って 1・2 で動く
//
// 相手の住所は「その相手が実際につないできたアドレス」を正とする（/hello を受けた側が
// socket の remoteAddress で覚え、ゴシップでもそれを配る）。90 秒便りが無い相手は不在。
// 投函の通知（NEW）は知っている仲間へ TCP で 1 回ずつ送り、UDP が通ればそちらでも流す。

const dgram = require('dgram');
const os = require('os');
const crypto = require('crypto');
const { EventEmitter } = require('events');
const { call } = require('./server');

const UDP_PORT = 47800;
const HTTP_PORT = 47801;
const HELLO_MS = 30 * 1000;
const STALE_MS = 90 * 1000;
const VERSION = 1;

function keyOf(passphrase) {
  return crypto.createHash('sha256').update(`agent-app-share:${String(passphrase || '')}`).digest('hex');
}

function broadcastAddresses() {
  const out = new Set(['255.255.255.255']);
  for (const list of Object.values(os.networkInterfaces())) {
    for (const ni of list || []) {
      if (ni.family !== 'IPv4' || ni.internal) continue;
      const ip = ni.address.split('.').map(Number);
      const mask = ni.netmask.split('.').map(Number);
      if (ip.length !== 4 || mask.length !== 4) continue;
      out.add(ip.map((b, i) => (b | (~mask[i] & 255))).join('.'));
    }
  }
  return [...out];
}

// "host" / "host:port" / "[v6]:port" → { address, port }
function parseSeed(text, defaultPort = HTTP_PORT) {
  const s = String(text || '').trim();
  if (!s) return null;
  const m = /^\[?([^\]]+?)\]?(?::(\d+))?$/.exec(s);
  if (!m) return null;
  return { address: m[1], port: Number(m[2]) || defaultPort };
}

function cleanAddress(address) {
  return String(address || '').replace(/^::ffff:/, '');
}

class Peers extends EventEmitter {
  // node     … 自分の名前。key … keyOf(合言葉)。info … HELLO に載せる宣言を返す関数
  // seeds    … 静的な仲間（文字列の配列）。udp … { port, targets } か false
  // httpPort … 自分の HTTP の受け口（listen 後に setHttpPort でも可）
  constructor({ node, key, info = () => ({}), seeds = [], udp = { port: UDP_PORT }, httpPort = 0, helloMs = HELLO_MS, staleMs = STALE_MS }) {
    super();
    this.node = String(node);
    this.key = String(key);
    this.info = info;
    this.seeds = (Array.isArray(seeds) ? seeds : []).map((s) => parseSeed(s)).filter(Boolean);
    this.udp = udp;
    this.httpPort = Number(httpPort) || 0;
    this.helloMs = helloMs;
    this.staleMs = staleMs;
    this.table = new Map();
    this.socket = null;
    this.timer = null;
    this.udpOk = false;
  }

  setHttpPort(port) { this.httpPort = Number(port) || 0; }

  async start() {
    if (this.udp) await this.bindUdp().catch(() => { this.udpOk = false; });
    this.timer = setInterval(() => { this.hello().catch(() => {}); }, this.helloMs);
    if (this.timer.unref) this.timer.unref();
    await this.hello().catch(() => {});
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.socket) { try { this.socket.close(); } catch { /* 既に閉じた */ } }
    this.socket = null;
    this.udpOk = false;
  }

  // ---- UDP（任意） ----------------------------------------------------------------

  bindUdp() {
    return new Promise((resolve, reject) => {
      const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
      socket.on('error', (err) => { this.udpOk = false; this.emit('udp-error', err); });
      socket.on('message', (buf, rinfo) => this.receiveUdp(buf, rinfo));
      socket.once('error', reject);
      socket.bind(this.udp.port || UDP_PORT, () => {
        try { socket.setBroadcast(true); } catch { /* loopback だけの環境 */ }
        this.socket = socket;
        this.udpOk = true;
        resolve();
      });
    });
  }

  sendUdp(type, payload = {}) {
    if (!this.socket) return;
    const msg = Buffer.from(JSON.stringify({ v: VERSION, type, node: this.node, port: this.httpPort, key: this.key, ...payload }));
    const targets = this.udp.targets || broadcastAddresses().map((address) => ({ address, port: this.udp.port || UDP_PORT }));
    for (const t of targets) {
      try { this.socket.send(msg, 0, msg.length, t.port, t.address, () => {}); } catch { /* そのアドレスには出せない */ }
    }
  }

  receiveUdp(buf, rinfo) {
    let msg;
    try { msg = JSON.parse(buf.toString('utf8')); } catch { return; }
    if (!msg || msg.v !== VERSION || !this.sameKey(msg.key)) return;
    const node = String(msg.node || '');
    if (!node || node === this.node) return;
    if (msg.type === 'HELLO') this.learn({ node, address: rinfo.address, port: msg.port, info: msg.info }, 'udp');
    else if (msg.type === 'NEW') { this.learn({ node, address: rinfo.address, port: msg.port }, 'udp'); this.emit('new', { id: String(msg.id || ''), node }); }
  }

  sameKey(key) {
    const x = Buffer.from(String(key || ''));
    const y = Buffer.from(this.key);
    return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
  }

  // ---- 仲間の表 -------------------------------------------------------------------

  learn({ node, address, port, info }, via) {
    const name = String(node || '');
    if (!name || name === this.node) return null;
    const before = this.table.get(name);
    const peer = {
      node: name,
      address: cleanAddress(address) || (before && before.address) || '',
      port: Number(port) || (before && before.port) || 0,
      info: info && typeof info === 'object' ? info : (before && before.info) || {},
      seenAt: Date.now(),
      via,
    };
    if (!peer.address || !peer.port) return null;
    this.table.set(name, peer);
    this.emit('peer', peer, { fresh: !before || (peer.seenAt - before.seenAt) > this.staleMs });
    return peer;
  }

  peers(now = Date.now()) {
    const out = [];
    for (const [node, peer] of this.table) {
      if (now - peer.seenAt > this.staleMs) { this.table.delete(node); continue; }
      out.push(peer);
    }
    return out.sort((a, b) => a.node.localeCompare(b.node));
  }

  // ゴシップに載せる分（info は載せない。相手が直接 /hello で取る）
  summary() {
    return this.peers().map((p) => ({ node: p.node, address: p.address, port: p.port, seenAt: p.seenAt }));
  }

  // ---- HELLO（TCP + UDP） ---------------------------------------------------------

  helloBody() {
    return { v: VERSION, node: this.node, port: this.httpPort, info: this.info(), peers: this.summary() };
  }

  async hello() {
    if (this.udpOk) this.sendUdp('HELLO', { info: this.info() });
    // 静的な仲間 + 知っている仲間へ TCP で。相手の返事に載った仲間も覚える（ゴシップ）
    const targets = new Map();
    for (const s of this.seeds) targets.set(`${s.address}:${s.port}`, s);
    for (const p of this.peers()) targets.set(`${p.address}:${p.port}`, p);
    await Promise.all([...targets.values()].map(async (t) => {
      try {
        const r = await call(t, 'POST', '/hello', { key: this.key, body: this.helloBody(), timeoutMs: 3000 });
        if (r.status !== 200 || !r.body || !r.body.node) return;
        this.learn({ node: r.body.node, address: t.address, port: t.port, info: r.body.info }, 'tcp');
        for (const p of r.body.peers || []) if (!this.table.has(p.node)) this.learn({ node: p.node, address: p.address, port: p.port }, 'gossip');
      } catch { /* いま届かない相手。次の HELLO で */ }
    }));
  }

  // /hello を受けたとき（server から呼ぶ）。返事に自分の宣言と仲間を載せる
  onHello(body, remoteAddress) {
    if (!body || body.v !== VERSION) return { status: 400, body: { error: '版が違います' } };
    this.learn({ node: body.node, address: remoteAddress, port: body.port, info: body.info }, 'tcp');
    for (const p of body.peers || []) if (!this.table.has(p.node)) this.learn({ node: p.node, address: p.address, port: p.port }, 'gossip');
    return { status: 200, body: this.helloBody() };
  }

  // 投函の通知。知っている仲間へ TCP で 1 回ずつ、UDP が通ればそちらでも
  async notifyNew(id) {
    if (this.udpOk) this.sendUdp('NEW', { id });
    await Promise.all(this.peers().map((p) => call(p, 'POST', '/notify', { key: this.key, body: { v: VERSION, type: 'NEW', node: this.node, port: this.httpPort, id }, timeoutMs: 3000 }).catch(() => {})));
  }

  onNotify(body, remoteAddress) {
    if (!body || body.v !== VERSION) return { status: 400, body: { error: '版が違います' } };
    this.learn({ node: body.node, address: remoteAddress, port: body.port }, 'tcp');
    if (body.type === 'NEW') this.emit('new', { id: String(body.id || ''), node: String(body.node || '') });
    return { status: 200, body: { ok: true } };
  }
}

module.exports = { Peers, keyOf, parseSeed, cleanAddress, broadcastAddresses, UDP_PORT, HTTP_PORT, HELLO_MS, STALE_MS };
