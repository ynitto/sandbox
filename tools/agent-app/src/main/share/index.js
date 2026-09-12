'use strict';

// 「共有」の配線。依頼者の側（requester）と参加者の側（participant）を 1 つの HTTP 受け口と
// 1 つの仲間表（peers）に載せ、ipc.js にはこの 1 つの入口だけを見せる。
//
//   start()      設定 share.enabled のときだけ動く。受け口を開け、仲間を探し、参加していれば拾い始める
//   post()       会話からの投函（runTurn の transport: 'shared' 分岐が呼ぶ）
//   status()     画面向けのまとめ（自分・仲間・列・実行中・今日の実績）
//   reconfigure()設定が変わったら受け口ごと立て直す
//
// 再起動のとき: 会話に「共有の依頼を待っている」印（session.share.id）が残っていて、
// 列にその依頼が無ければ、失敗のメッセージを会話へ残して印を消す。

const os = require('os');
const path = require('path');
const store = require('../store');
const { Peers, keyOf, HTTP_PORT } = require('./peers');
const { createServer } = require('./server');
const { Requester } = require('./requester');
const { Participant } = require('./participant');
const { Ledger } = require('./ledger');

const SHARE_KEYS = ['enabled', 'node', 'passphrase', 'port', 'udp', 'peers'];

function normalizeNode(name) {
  const s = String(name || '').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 60);
  return s || 'node';
}

function defaultNode() {
  let user = '';
  try { user = os.userInfo().username; } catch { user = ''; }
  return normalizeNode(`${user || 'user'}.${os.hostname().split('.')[0]}`);
}

function sameShare(a, b) {
  return SHARE_KEYS.every((k) => JSON.stringify(a && a[k]) === JSON.stringify(b && b[k]));
}

class Share {
  // userData … 保存先。config … 正規化済みの設定（share を含む）。send … renderer へのイベント
  // runPrompt… CLI を 1 回起こす関数（ipc.js が持つ）。agents … () => 使える CLI 名。repoFor … (url) => 登録フォルダ
  // options … テスト用: udp（false で切る）、peersOptions、timers
  constructor({ userData, config, send = () => {}, runPrompt, agents = () => [], repoFor = () => '', options = {} }) {
    this.userData = userData;
    this.config = config;
    this.send = send;
    this.runPrompt = runPrompt;
    this.agents = agents;
    this.repoFor = repoFor;
    this.options = options;
    this.state = 'off';
    this.port = 0;
    this.node = '';
    this.peers = null;
    this.server = null;
    this.requester = null;
    this.participant = null;
    this.ledger = null;
    this.error = '';
  }

  get cfg() { return (this.config && this.config.share) || {}; }

  async start() {
    const cfg = this.cfg;
    if (!cfg.enabled) { this.state = 'off'; return this; }
    this.node = normalizeNode(cfg.node || defaultNode());
    const key = keyOf(cfg.passphrase);
    const dir = path.join(this.userData, 'share');
    this.ledger = new Ledger(path.join(dir, 'ledger'), { now: this.options.now });
    this.requester = new Requester({
      userData: this.userData, node: this.node, key, file: path.join(dir, 'requests.json'), send: this.send,
      notify: (id) => (this.peers ? this.peers.notifyNew(id) : Promise.resolve()),
      ...(this.options.requester || {}),
    });
    this.peers = new Peers({
      node: this.node, key, seeds: cfg.peers, udp: this.options.udp === undefined ? (cfg.udp === false ? false : { port: cfg.udpPort || undefined }) : this.options.udp,
      info: () => (this.participant ? this.participant.nodeInfo() : { node: this.node }),
      ...(this.options.peers || {}),
    });
    this.participant = new Participant({
      userData: this.userData, node: this.node, key, peers: this.peers, ledger: this.ledger,
      settings: () => this.cfg, agents: this.agents, repoFor: this.repoFor, runPrompt: this.runPrompt, send: this.send,
      file: path.join(dir, 'outbox.json'), now: this.options.now,
      ...(this.options.participant || {}),
    });
    this.server = createServer({
      key,
      handlers: {
        hello: (body, remote) => this.peers.onHello(body, remote),
        notify: (body, remote) => this.peers.onNotify(body, remote),
        node: () => ({ status: 200, body: this.participant.nodeInfo() }),
        requests: () => ({ status: 200, body: this.requester.list() }),
        claim: (id, body, remote) => this.requester.claim(id, body, remote),
        heartbeat: (id, body, remote) => this.requester.heartbeat(id, body, remote),
        result: (id, body, remote) => this.requester.result(id, body, remote),
        cancel: (id, body) => this.participant.cancel(id, body),
        // ひとことは両向き。自分が出した依頼なら依頼者として、引き受けた依頼なら執行者として受ける
        message: (id, body) => (this.requester.get(id) ? this.requester.message(id, body) : this.participant.message(id, body)),
        attachment: (id, name) => this.requester.attachmentPath(id, name),
      },
    });
    // 設定のポート。0 は「空いているポート」（1 台の PC で 2 つ動かすときや、試験のとき）。
    // 数として読めないものだけ既定へ倒す——0 を既定に読み替えると、意図した「空き」が塞がった 47801 になる。
    const wanted = Number(cfg.port);
    const port = this.options.port != null ? this.options.port : (Number.isFinite(wanted) && wanted >= 0 ? wanted : HTTP_PORT);
    try {
      this.port = await this.server.listen(port, this.options.host || '0.0.0.0');
    } catch (err) {
      this.state = 'error';
      this.error = `受け口を開けません（ポート ${port}）: ${err.message}`;
      this.server = null;
      return this;
    }
    this.peers.setHttpPort(this.port);
    this.requester.start();
    const changed = () => this.send('share:changed', this.status());
    this.requester.on('changed', changed);
    this.participant.on('changed', changed);
    this.peers.on('peer', (_p, { fresh }) => { if (fresh) changed(); });
    this.participant.start();
    await this.peers.start();
    this.recoverSessions();
    this.state = 'on';
    this.error = '';
    return this;
  }

  async stop() {
    if (this.participant) this.participant.stop();
    if (this.requester) this.requester.stop();
    if (this.peers) this.peers.stop();
    if (this.server) await this.server.close().catch(() => {});
    this.participant = null;
    this.requester = null;
    this.peers = null;
    this.server = null;
    this.state = 'off';
    this.port = 0;
  }

  async reconfigure(config) {
    const before = this.cfg;
    this.config = config;
    if (!sameShare(before, this.cfg) || (this.state !== 'on' && this.cfg.enabled)) {
      await this.stop();
      await this.start();
    }
    this.send('share:changed', this.status());
    return this.status();
  }

  // 再起動のあと、待っていた依頼が列に残っていなければ会話に 1 行残して印を消す
  recoverSessions() {
    let sessions = [];
    try { sessions = store.readAllSessions(this.userData); } catch { return; }
    for (const sess of sessions) {
      const id = sess && sess.share && sess.share.id;
      if (!id) continue;
      const r = this.requester.get(id);
      if (r && (r.state === 'open' || r.state === 'working')) continue;
      const message = {
        role: 'assistant', cli: '', model: '', policy: 'shared', tier: '', text: '（共有の依頼はアプリの再起動で失われた。もう一度送ってほしい）',
        code: 1, elapsedMs: 0, stopped: true, parts: { thinking: [], information: [] }, error: '', share: { id, node: '', cli: '' },
      };
      try { store.appendMessage(this.userData, sess.id, message); store.updateSession(this.userData, sess.id, { share: null }); } catch { /* 会話が壊れていれば触らない */ }
    }
  }

  status() {
    const cfg = this.cfg;
    const on = this.state === 'on';
    return {
      enabled: !!cfg.enabled, state: this.state, error: this.error, node: this.node || normalizeNode(cfg.node || defaultNode()), port: this.port,
      udp: !!(on && this.peers && this.peers.udpOk), participate: !!cfg.participate,
      accept: on ? this.participant.mode() : (cfg.accept || 'off'), capacity: on ? this.participant.capacity() : 0,
      me: on ? this.participant.nodeInfo() : null,
      peers: on ? this.peers.peers().map((p) => ({ node: p.node, address: p.address, port: p.port, seenAt: p.seenAt, via: p.via, info: p.info })) : [],
      mine: on ? this.requester.view() : [],
      others: on ? this.participant.gatheredView() : [],
      inflight: on ? this.participant.inflightView() : [],
      today: on ? this.ledger.today() : null,
    };
  }

  post(input, opts) {
    if (this.state !== 'on') throw new Error(this.error || '共有が動いていません（設定 > 共有）');
    return this.requester.post(input, opts);
  }

  // 画面から 1 件を選んで引き受ける／引き受けた実行を止める
  accept(id) {
    if (this.state !== 'on') throw new Error(this.error || '共有が動いていません（設定 > 共有）');
    return this.participant.accept(id);
  }

  stopAccepted(id) { return this.participant ? this.participant.stopInflight(id) : false; }

  // ひとことを送る。自分が出した依頼なら執行者へ、引き受けた依頼なら依頼者へ
  say(id, text) {
    if (this.state !== 'on') throw new Error(this.error || '共有が動いていません（設定 > 共有）');
    return this.requester.get(id) ? this.requester.say(id, text) : this.participant.say(id, text);
  }

  // 引き受けた依頼の端末へキーを送る（自分の PC の CLI だけ）
  keys(id, data) { return this.participant ? this.participant.keys(id, data) : false; }

  // 端末の画面（依頼者として待っている分と、自分が引き受けている分の両方）
  screenOf(id) {
    if (this.state !== 'on') return '';
    return this.requester.screenOf(id) || this.participant.screenOf(id) || '';
  }

  cancelSession(sessionId) { return this.requester ? this.requester.cancelSession(sessionId) : null; }
  cancel(id) { return this.requester ? this.requester.cancel(id, '利用者が取り下げた') : false; }
  setPriority(id, priority) { return this.requester ? this.requester.setPriority(id, priority) : null; }
  pendingSessionIds() { return this.requester ? this.requester.pendingSessionIds() : []; }
  knownClis() {
    const out = new Set();
    if (!this.peers) return [];
    for (const p of this.peers.peers()) for (const cli of (p.info && p.info.agent_cli) || []) out.add(cli);
    return [...out].sort();
  }
}

module.exports = { Share, normalizeNode, defaultNode, sameShare, SHARE_KEYS };
