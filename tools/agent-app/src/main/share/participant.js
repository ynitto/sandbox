'use strict';

// 参加者の側。余っている枠を差し出し、仲間の依頼を拾って自分の CLI で答える。
//
//   引き受け方（設定 share.accept）は 3 つ。
//     auto   … 10 秒ごと（と NEW を聞いたとき）自分で拾う
//     manual … 画面で選ばれた 1 件だけ拾う（accept(id)）
//     off    … 拾わない（依頼は出せる）
//   auto のとき 10 秒ごと:
//     全員の /requests を集める → queue で並べて資格を見る → 上から claim → 200 が返った 1 件を実行
//   実行中: 30 秒ごとに heartbeat。依頼者に届かなくなったら（2 回続けて失敗）CLI を止める（枠を捨てない）
//   終了: 台帳に 1 行、result を依頼者へ直送。届かなければ outbox に持って 60 秒ごとに再送（24 時間）
//
// 実行は tmux の画面で行い（ipc の runPrompt が決める）、その画面を心拍に載せて依頼者へ送る。
// 依頼者はそれを自分の端末ミラーに描くので、他人の PC で何が起きているかを見ながら待てる。
//
// 読み取り専用で起こす。cwd は依頼の workspace と一致する登録リポジトリか、空の scratch フォルダ。
// 書き込みの依頼は設定で受けると決めたときだけ拾う（成果の納品はまだ無い。設計 §5.2）。

const fs = require('fs');
const path = require('path');
const { EventEmitter } = require('events');
const queue = require('./queue');
const { call, download } = require('./server');

const TICK_MS = 10 * 1000;
const HEARTBEAT_MS = 30 * 1000;
const LEASE_MS = 15 * 60 * 1000;
const OUTBOX_RETRY_MS = 60 * 1000;
const SCREEN_MS = 2 * 1000;
const MAX_SCREEN = 48 * 1024;
const OUTBOX_TTL_MS = 24 * 60 * 60 * 1000;
const GATHER_TIMEOUT_MS = 3000;

function nowIso() { return new Date().toISOString(); }

// 拾えない理由を、画面にそのまま出せる 1 行にする
const REASONS = {
  state: 'その依頼はもう受け付けていません',
  own: '自分が出した依頼です',
  write: '書き込みの依頼は受けない設定です',
  cli: '依頼が指定するエージェントをこの PC は提供していません',
  quota: 'この PC のエージェントは今日の枠を使い切っています',
  requester_cap: 'この依頼者から今日受けられる件数に達しています',
  repo: '依頼のリポジトリをこの PC に登録していません',
};
function reasonText(reason) {
  return REASONS[reason] || '受けられません';
}

function safeName(name) {
  return String(name || '').replace(/[\\/]/g, '_').replace(/^\.+/, '_').slice(0, 120) || 'file';
}

class Participant extends EventEmitter {
  // peers    … Peers（仲間の表と通知）。ledger … Ledger
  // settings … () => 設定 share（participate / clis / acceptWrite / maxConcurrent / dailyCap / perRequesterDailyCap）
  // agents   … () => この PC で使える CLI 名の配列。repoFor … (url) => 登録リポジトリのフォルダ or ''
  // runPrompt… ({ cli, prompt, model, readonly, cwd, files, timeoutMs, onLine }) => { done: Promise, stop(reason) }
  constructor({ userData, node, key, peers, ledger, settings, agents, repoFor = () => '', runPrompt, send = () => {}, file,
    tickMs = TICK_MS, heartbeatMs = HEARTBEAT_MS, leaseMs = LEASE_MS, outboxRetryMs = OUTBOX_RETRY_MS, screenMs = SCREEN_MS, now = () => Date.now() }) {
    super();
    this.userData = userData;
    this.node = String(node);
    this.key = String(key || '');
    this.peers = peers;
    this.ledger = ledger;
    this.settings = settings;
    this.agents = agents;
    this.repoFor = repoFor;
    this.runPrompt = runPrompt;
    this.send = send;
    this.file = file || path.join(userData, 'share', 'outbox.json');
    this.tickMs = tickMs;
    this.heartbeatMs = heartbeatMs;
    this.leaseMs = leaseMs;
    this.outboxRetryMs = outboxRetryMs;
    this.screenMs = screenMs;
    this.now = now;
    this.inflight = new Map();
    this.outbox = [];
    this.lastGathered = [];
    this.busy = false;
    this.timer = null;
    this.outboxTimer = null;
    this.onNew = () => { this.tick().catch(() => {}); };
  }

  start() {
    this.loadOutbox();
    this.timer = setInterval(() => { this.tick().catch(() => {}); }, this.tickMs);
    if (this.timer.unref) this.timer.unref();
    this.outboxTimer = setInterval(() => { this.flushOutbox().catch(() => {}); }, this.outboxRetryMs);
    if (this.outboxTimer.unref) this.outboxTimer.unref();
    this.peers.on('new', this.onNew);
    return this;
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    if (this.outboxTimer) clearInterval(this.outboxTimer);
    this.timer = null;
    this.outboxTimer = null;
    this.peers.off('new', this.onNew);
    for (const item of this.inflight.values()) {
      clearInterval(item.screenTimer);
      try { item.controller.stop('参加を止めた'); } catch { /* 既に終わった */ }
    }
  }

  // ---- 宣言（HELLO と /node に載せる） -----------------------------------------------------

  // 引き受け方。'auto' | 'manual' | 'off'
  mode() {
    const cfg = this.settings() || {};
    if (['auto', 'manual', 'off'].includes(cfg.accept)) return cfg.accept;
    return cfg.participate ? 'auto' : 'off';
  }

  offeredClis() {
    const cfg = this.settings() || {};
    const available = this.agents() || [];
    const chosen = Array.isArray(cfg.clis) && cfg.clis.length ? cfg.clis : available;
    return chosen.filter((name) => available.includes(name));
  }

  nodeInfo() {
    const cfg = this.settings() || {};
    const clis = this.offeredClis();
    const budget = this.ledger.canAccept({ participate: this.mode() !== 'off', clis, maxConcurrent: cfg.maxConcurrent, dailyCap: cfg.dailyCap, inflight: this.inflight.size });
    return {
      node: this.node, contract_version: 1, workloads: ['turn'], modes: cfg.acceptWrite ? ['read', 'write'] : ['read'],
      agent_cli: clis, participate: this.mode() !== 'off', accept: this.mode(), max_concurrent: Math.max(1, Number(cfg.maxConcurrent) || 1),
      can_accept: budget.can_accept, reason_codes: budget.reason_codes, clis: budget.clis, turns: { ...budget.today, per_requester_cap: Number(cfg.perRequesterDailyCap) || 0 },
      inflight: [...this.inflight.values()].map((i) => ({ id: i.request.id, posted_by: i.request.posted_by, cli: i.cli, started_at: i.startedAt })),
    };
  }

  // ---- 拾う ------------------------------------------------------------------------------

  async gather() {
    const peers = this.peers.peers();
    const lists = await Promise.all(peers.map(async (peer) => {
      try {
        const r = await call(peer, 'GET', '/requests', { key: this.key, timeoutMs: GATHER_TIMEOUT_MS });
        if (r.status !== 200 || !Array.isArray(r.body)) return [];
        return r.body.map((req) => ({ ...req, peer }));
      } catch { return []; }
    }));
    this.lastGathered = lists.flat();
    return this.lastGathered;
  }

  // 同時数と 1 日の上限から見た空き（引き受け方は見ない）
  capacity() {
    const cfg = this.settings() || {};
    const max = Math.max(1, Number(cfg.maxConcurrent) || 1);
    const cap = Number(cfg.dailyCap) || 0;
    if (cap > 0 && this.ledger.today().count >= cap) return 0;
    return Math.max(0, max - this.inflight.size);
  }

  // 自分で拾う分の空き。選んで受ける・受けないときは 0（画面から accept で拾う）
  slots() {
    return this.mode() === 'auto' ? this.capacity() : 0;
  }

  context() {
    const cfg = this.settings() || {};
    return {
      node: this.node, clis: this.offeredClis(), cliOk: (cli) => this.ledger.cliOk(cli), acceptWrite: !!cfg.acceptWrite,
      repoFor: this.repoFor, servedToday: this.ledger.today().byRequester, perRequesterCap: Number(cfg.perRequesterDailyCap) || 0,
    };
  }

  async tick() {
    if (this.busy) return [];
    this.busy = true;
    const started = [];
    try {
      const slots = this.slots();
      const all = await this.gather();
      if (!slots || !all.length) return started;
      const skip = new Set(this.inflight.keys());
      const candidates = queue.pick(all.filter((r) => !skip.has(r.id)), this.context(), { now: this.now(), slots: slots + 3 });
      for (const { request, cli, mode } of candidates) {
        if (started.length >= slots) break;
        const claimed = await this.claim(request, cli);
        if (!claimed) continue;
        started.push(request.id);
        this.run({ request, cli, mode, peer: request.peer, post: claimed }).catch(() => {});
      }
      return started;
    } finally {
      this.busy = false;
      if (started.length) this.emit('changed');
    }
  }

  async claim(request, cli) {
    try {
      const r = await call(request.peer, 'POST', `/requests/${encodeURIComponent(request.id)}/claim`, { key: this.key, body: { who: this.node, port: this.peers.httpPort, cli }, timeoutMs: GATHER_TIMEOUT_MS });
      return r.status === 200 ? r.body : null;
    } catch { return null; }
  }

  // 画面から 1 件を選んで引き受ける。理由が立たなければ文言を返す（押せない理由を画面に出す）
  async accept(id) {
    const wanted = String(id || '');
    if (this.mode() === 'off') throw new Error('「受けない」にしています（設定 > 共有）');
    if (this.inflight.has(wanted)) throw new Error('この依頼は既に引き受けています');
    if (!this.capacity()) throw new Error('同時に受けられる数か、1 日の上限に達しています');
    const all = this.lastGathered.length ? this.lastGathered : await this.gather();
    const request = all.find((r) => r.id === wanted);
    if (!request) throw new Error('その依頼は見つかりません（取り下げられたか、誰かが拾いました）');
    const verdict = queue.eligible(request, this.context());
    if (!verdict.ok) throw new Error(reasonText(verdict.reason));
    const claimed = await this.claim(request, verdict.cli);
    if (!claimed) throw new Error('先に誰かが拾いました');
    this.run({ request, cli: verdict.cli, mode: verdict.mode, peer: request.peer, post: claimed }).catch(() => {});
    this.emit('changed');
    return { id: wanted, cli: verdict.cli };
  }

  // 引き受けた実行を自分から止める
  stopInflight(id) {
    const item = this.inflight.get(String(id));
    if (!item) return false;
    item.controller.stop('引き受けた人が止めた');
    return true;
  }

  // ---- 実行 ------------------------------------------------------------------------------

  async run({ request, cli, mode, peer, post }) {
    const id = request.id;
    const startedAt = this.now();
    const scratch = path.join(this.userData, 'share', 'scratch', id);
    fs.mkdirSync(scratch, { recursive: true });
    const repoDir = post.workspace && post.workspace.url ? this.repoFor(post.workspace.url) : '';
    const cwd = repoDir || scratch;
    let prompt = String(post.goal || '');
    const files = [];
    for (const name of post.attachments || []) {
      const target = path.join(scratch, 'attachments', safeName(name));
      fs.mkdirSync(path.dirname(target), { recursive: true });
      try { await download(peer, `/requests/${encodeURIComponent(id)}/attachments/${encodeURIComponent(name)}`, target, { key: this.key }); files.push(target); } catch { /* 取れない添付は本文の案内から外す */ }
    }
    if (files.length) prompt = `${prompt}\n\n添付ファイル（必要に応じて読んで参照すること）:\n${files.map((f) => `- ${f}`).join('\n')}`;
    const item = { request, peer, cli, mode, controller: null, startedAt, misses: 0, hb: null, screenTimer: null, screen: '', sentScreen: '' };
    const controller = this.runPrompt({
      cli, prompt, model: post.model || '', readonly: true, cwd, files, timeoutMs: this.leaseMs, onLine: () => {},
      shareId: id,
      onScreen: (text) => {
        item.screen = String(text || '').slice(-MAX_SCREEN);
        this.send('share:screen', { id, text: item.screen, node: this.node, cli, mine: true });
      },
    });
    item.controller = controller;
    this.inflight.set(id, item);
    item.hb = setInterval(() => { this.heartbeat(item).catch(() => {}); }, this.heartbeatMs);
    if (item.hb.unref) item.hb.unref();
    // 画面は変わったときだけ心拍に載せて送る（依頼者の端末ミラーがこれを描く）
    item.screenTimer = setInterval(() => {
      if (!item.screen || item.screen === item.sentScreen) return;
      item.sentScreen = item.screen;
      this.heartbeat(item, { screen: item.screen }).catch(() => {});
    }, this.screenMs);
    if (item.screenTimer.unref) item.screenTimer.unref();
    this.emit('changed');
    let outcome;
    try {
      outcome = await controller.done;
    } catch (err) {
      outcome = { text: '', code: 1, stopped: false, error: (err && err.message) || String(err), errorClass: 'cli', elapsedMs: this.now() - startedAt };
    }
    clearInterval(item.hb);
    clearInterval(item.screenTimer);
    this.inflight.delete(id);
    const status = outcome.stopped ? 'cancelled' : (outcome.code === 0 && outcome.text ? 'done' : 'failed');
    const errorClass = status === 'failed' ? String(outcome.errorClass || 'cli') : '';
    if (errorClass === 'quota') this.ledger.markQuota(cli, outcome.quotaKind === 'rate_limit' ? 'rate_limit' : 'exhausted');
    this.ledger.record({ id, posted_by: request.posted_by, cli, model: post.model || '', mode, started_at: new Date(startedAt).toISOString(), seconds: (this.now() - startedAt) / 1000, status, error_class: errorClass, tokens_in: outcome.usage ? outcome.usage.tokens_in : null, tokens_out: outcome.usage ? outcome.usage.tokens_out : null });
    try { fs.rmSync(scratch, { recursive: true, force: true }); } catch { /* 残っても害は無い */ }
    if (status === 'cancelled') { this.emit('changed'); return null; }
    const result = {
      who: this.node, status, answer: status === 'done' ? outcome.text : '', error: status === 'done' ? '' : String(outcome.error || ''), error_class: errorClass,
      agent_cli: cli, model: post.model || '', elapsed_ms: this.now() - startedAt, usage: outcome.usage || null,
    };
    await this.deliver({ id, peer, result, queuedAt: this.now() });
    this.emit('changed');
    return result;
  }

  async heartbeat(item, { screen = '' } = {}) {
    try {
      const r = await call(item.peer, 'POST', `/requests/${encodeURIComponent(item.request.id)}/heartbeat`, { key: this.key, body: { who: this.node, port: this.peers.httpPort, cli: item.cli, progress: '', ...(screen ? { screen } : {}) }, timeoutMs: GATHER_TIMEOUT_MS });
      if (r.status === 409 || r.status === 404) { item.controller.stop('依頼者が取り下げたか、別の人に渡った'); return; }
      item.misses = 0;
    } catch {
      item.misses += 1;
      if (item.misses >= 2) item.controller.stop('依頼者に届かない');
    }
  }

  // 依頼者からの取り下げ（server が呼ぶ）
  cancel(id) {
    const item = this.inflight.get(String(id));
    if (!item) return { status: 404, body: { error: '実行していません' } };
    item.controller.stop('依頼者が取り下げた');
    return { status: 200, body: { ok: true } };
  }

  // ---- 答えの直送と outbox ---------------------------------------------------------------

  async deliver(entry) {
    const ok = await this.send1(entry);
    if (!ok) { this.outbox.push(entry); this.saveOutbox(); }
    return ok;
  }

  async send1(entry) {
    try {
      const r = await call(entry.peer, 'POST', `/requests/${encodeURIComponent(entry.id)}/result`, { key: this.key, body: entry.result, timeoutMs: 10000 });
      return r.status === 200 || r.status === 409 || r.status === 404;   // 409/404 = もう要らない
    } catch { return false; }
  }

  async flushOutbox() {
    if (!this.outbox.length) return;
    const keep = [];
    for (const entry of this.outbox) {
      if (this.now() - entry.queuedAt > OUTBOX_TTL_MS) continue;
      // 相手の住所が変わっていれば仲間の表から引き直す
      const live = this.peers.peers().find((p) => p.node === entry.peer.node);
      const target = { ...entry, peer: live ? { ...entry.peer, address: live.address, port: live.port } : entry.peer };
      if (!(await this.send1(target))) keep.push(entry);
    }
    this.outbox = keep;
    this.saveOutbox();
  }

  loadOutbox() {
    try { this.outbox = JSON.parse(fs.readFileSync(this.file, 'utf8')).filter((e) => e && e.id && e.peer && e.result); } catch { this.outbox = []; }
  }

  saveOutbox() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(this.file, JSON.stringify(this.outbox, null, 2), 'utf8');
  }

  inflightView() {
    return [...this.inflight.values()].map((i) => ({ id: i.request.id, posted_by: i.request.posted_by, title: i.request.title, cli: i.cli, started_at: new Date(i.startedAt).toISOString(), host: i.peer ? i.peer.node : '' }));
  }

  // 画面向け。仲間から集めた依頼に「いま拾えるか」と、拾えない理由の 1 行を添える
  gatheredView() {
    const ctx = this.context();
    const capacity = this.capacity();
    return this.lastGathered.map(({ peer, ...request }) => {
      const verdict = queue.eligible(request, ctx);
      const full = verdict.ok && !capacity;
      return {
        ...request, host: peer ? peer.node : '',
        canAccept: verdict.ok && !!capacity,
        cli: verdict.ok ? verdict.cli : '',
        reason: full ? '同時に受けられる数か、1 日の上限に達しています' : (verdict.ok ? '' : reasonText(verdict.reason)),
      };
    });
  }

  screenOf(id) {
    const item = this.inflight.get(String(id));
    return item ? item.screen : '';
  }
}

module.exports = { Participant, TICK_MS, HEARTBEAT_MS, LEASE_MS, OUTBOX_RETRY_MS, OUTBOX_TTL_MS, SCREEN_MS, MAX_SCREEN, safeName, nowIso, reasonText, REASONS };
