'use strict';

// 依頼者の側。自分が投函した依頼の列を持ち、その依頼の調停役になる。
//
//   open ── claim（先着 1 件だけ 200）──▶ working ── result ──▶ done / failed
//    ▲                                       │ 心拍が 90 秒途絶（執行者が消えた）
//    └───────────────────────────────────────┘ 列へ戻し、仲間へもう一度 NEW
//   open / working ── cancel（利用者の取り下げ）──▶ cancelled
//
// 依頼にはもう 1 本、**人と人のやり取り**（ひとこと）がぶら下がる。CLI には入らず、引き受けた人が
// 読んで、何を打つかを自分で決める。依頼が終われば一緒に終わる（会話としては残さない）。
//
// 依頼ごとに調停役が 1 つ（この agent-app）なので、勝者は必ず 1 人。分散 claim も lease の同期も無い。
// 答えは執行者から直接届き、その会話に assistant のメッセージとして保存する。
// 依頼は requests.json に残し、再起動しても列が消えない（執行者が消えていれば心拍の途絶で戻る）。
//
// 依頼者が落ちると依頼は誰にも見えなくなる（受け取る相手がいないので実害はない）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { EventEmitter } = require('events');
const store = require('../store');
const { call } = require('./server');
const { priorityOf } = require('./queue');

const WATCHDOG_MS = 90 * 1000;
const TICK_MS = 15 * 1000;
const MAX_ATTEMPTS = 2;                 // 参加者側の枠切れ・一過性の失敗は 1 回だけ黙って再投函する
const RETRY_CLASSES = ['quota', 'transient'];
const KEEP_TERMINAL = 50;
const MAX_ANSWER = 200 * 1024;
const MAX_SCREEN = 48 * 1024;
const MAX_SUMMARY = 600;
const MAX_TALK = 500;              // ひとこと 1 件の長さ
const KEEP_TALK = 50;              // 1 つの依頼に残すひとことの数

function nowIso() { return new Date().toISOString(); }

function newId() {
  const ts = nowIso().replace(/[-:T]/g, '').slice(0, 14);
  return `dg-${ts}-${crypto.randomBytes(2).toString('hex')}`;
}

class Requester extends EventEmitter {
  // userData … 会話の保存先。node … 自分の名前。key … 合言葉の鍵（執行者へ取り下げを届けるとき）
  // send     … renderer へのイベント。notify … 仲間へ NEW を流す関数（async）
  constructor({ userData, node, key, file, send = () => {}, notify = async () => {}, watchdogMs = WATCHDOG_MS, tickMs = TICK_MS, now = () => Date.now() }) {
    super();
    this.userData = userData;
    this.node = String(node);
    this.key = String(key || '');
    this.file = file || path.join(userData, 'share', 'requests.json');
    this.send = send;
    this.notify = notify;
    this.watchdogMs = watchdogMs;
    this.tickMs = tickMs;
    this.now = now;
    this.requests = new Map();
    this.screens = new Map();       // 依頼 id → 執行者の端末の画面（心拍で届く。保存しない）
    this.done = new Map();          // 依頼 id → turnGate の release など、終わったら呼ぶもの（メモリだけ）
    this.timer = null;
    this.servedToday = { day: '', count: 0 };
  }

  // ---- 保存 ------------------------------------------------------------------------

  load() {
    try {
      const raw = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      for (const r of Array.isArray(raw.requests) ? raw.requests : []) {
        if (!r || !r.id) continue;
        if (!Array.isArray(r.talk)) r.talk = [];
        // 再起動後: 執行者は心拍で戻ってくるか、途絶で列へ戻る
        this.requests.set(r.id, r);
      }
      if (raw.servedToday && typeof raw.servedToday === 'object') this.servedToday = raw.servedToday;
    } catch { /* まだ無い */ }
    return this;
  }

  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp.${process.pid}`;
    fs.writeFileSync(tmp, JSON.stringify({ requests: [...this.requests.values()], servedToday: this.servedToday }, null, 2), 'utf8');
    fs.renameSync(tmp, this.file);
  }

  start() {
    this.load();
    this.timer = setInterval(() => this.watchdog(), this.tickMs);
    if (this.timer.unref) this.timer.unref();
    return this;
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  // ---- 投函 ------------------------------------------------------------------------

  // input: { sessionId, title, goal, requires: { agent_cli: [] }, mode, model, priority, attachments: [{ id, name, path }],
  //          workspace: { url, base }, retryOf, attempts }
  post(input, { onDone = null } = {}) {
    const id = newId();
    const request = {
      id,
      state: 'open',
      posted_by: this.node,
      posted_at: nowIso(),
      title: String(input.title || String(input.goal || '').split('\n')[0]).slice(0, 60),
      // 引き受ける人が中身を見て決められるよう、利用者が書いた依頼文だけを短く添える
      // （合成した本文（goal）は履歴も含むので配らない。全文は claim した人にだけ渡す）
      summary: String(input.summary || input.title || '').slice(0, MAX_SUMMARY),
      priority: priorityOf(input.priority),
      mode: input.mode === 'write' ? 'write' : 'read',
      requires: { agent_cli: [...new Set((Array.isArray(input.requires && input.requires.agent_cli) ? input.requires.agent_cli : []).map((c) => String(c || '').trim().toLowerCase()).filter(Boolean))] },
      model: String(input.model || ''),
      workspace: input.workspace && input.workspace.url ? { url: String(input.workspace.url), base: String(input.workspace.base || '') } : null,
      goal: String(input.goal || ''),
      attachments: (Array.isArray(input.attachments) ? input.attachments : []).map((a) => ({ name: String(a.name || ''), path: String(a.path || '') })).filter((a) => a.name && a.path),
      talk: [],
      sessionId: String(input.sessionId || ''),
      retry_of: String(input.retryOf || ''),
      attempts: Number(input.attempts) || 1,
      executor: null,
      claimed_at: '',
      last_heartbeat: 0,
      progress: [],
      result: null,
      finished_at: '',
    };
    this.requests.set(id, request);
    if (onDone) this.done.set(id, onDone);
    this.prune();
    this.save();
    this.notify(id).catch(() => {});
    this.emit('changed');
    return request;
  }

  get(id) { return this.requests.get(String(id)) || null; }

  // 執行者から届いている最新の画面（会話を開き直したときに、まずこれを描く）
  screenOf(id) { return this.screens.get(String(id)) || ''; }

  // ---- ひとこと（人と人） ---------------------------------------------------------

  appendTalk(r, entry) {
    r.talk.push(entry);
    if (r.talk.length > KEEP_TALK) r.talk.splice(0, r.talk.length - KEEP_TALK);
    this.save();
    this.emit('changed');
    return entry;
  }

  // 執行者から届いたひとこと（server が呼ぶ）
  message(id, body = {}) {
    const r = this.get(id);
    if (!r) return { status: 404, body: { error: 'その依頼はありません' } };
    const who = String(body.who || '').trim();
    if (!r.executor || r.executor.node !== who) return { status: 409, body: { error: 'この依頼の執行者ではありません' } };
    const text = String(body.text || '').trim().slice(0, MAX_TALK);
    if (!text) return { status: 400, body: { error: '本文がありません' } };
    this.appendTalk(r, { who, text, at: nowIso() });
    return { status: 200, body: { ok: true } };
  }

  // 依頼者から執行者へひとことを送る（画面が呼ぶ）。届かなければ印を付けて次の巡回で送り直す
  async say(id, text) {
    const r = this.get(id);
    if (!r) throw new Error('その依頼はありません');
    const body = String(text || '').trim().slice(0, MAX_TALK);
    if (!body) throw new Error('送る内容がありません');
    const entry = this.appendTalk(r, { who: this.node, text: body, at: nowIso(), ...(r.executor ? {} : { pending: true }) });
    if (r.executor) await this.deliverTalk(r, entry);
    return entry;
  }

  async deliverTalk(r, entry) {
    const executor = r.executor;
    if (!executor || !executor.address || !executor.port) { entry.pending = true; this.save(); this.emit('changed'); return false; }
    try {
      const res = await call({ address: executor.address, port: executor.port }, 'POST', `/requests/${encodeURIComponent(r.id)}/message`,
        { key: this.key, body: { who: this.node, text: entry.text, at: entry.at }, timeoutMs: 5000 });
      const ok = res.status === 200;
      if (ok) delete entry.pending;
      else entry.pending = true;
      this.save();
      this.emit('changed');
      return ok;
    } catch {
      entry.pending = true;
      this.save();
      this.emit('changed');
      return false;
    }
  }

  // まだ届いていないひとことを送り直す（watchdog の巡回から）
  async flushTalk() {
    for (const r of this.requests.values()) {
      if (!r.executor || !(r.state === 'open' || r.state === 'working')) continue;
      for (const entry of r.talk) {
        if (entry.pending && entry.who === this.node) await this.deliverTalk(r, entry);
      }
    }
  }

  // /requests に出す分（本文は claim のときに渡す）
  list() {
    return [...this.requests.values()]
      .filter((r) => r.state === 'open' || r.state === 'working')
      .map((r) => this.publicView(r));
  }

  publicView(r) {
    return {
      id: r.id, state: r.state, posted_by: r.posted_by, posted_at: r.posted_at, title: r.title, summary: r.summary,
      priority: r.priority, mode: r.mode, requires: r.requires, model: r.model, workspace: r.workspace,
      attachments: r.attachments.map((a) => a.name),
      executor: r.executor ? r.executor.node : '', claimed_at: r.claimed_at,
      requester_served_today: this.served(),
      retry_of: r.retry_of, attempts: r.attempts, finished_at: r.finished_at,
      result: r.result ? { status: r.result.status, agent_cli: r.result.agent_cli, elapsed_ms: r.result.elapsed_ms, error_class: r.result.error_class } : null,
    };
  }

  // 画面向け（終端も含む。新しい順）。会話 ID と答えは自分の画面にだけ出す
  view() {
    return [...this.requests.values()].sort((a, b) => String(b.posted_at).localeCompare(String(a.posted_at))).map((r) => ({
      ...this.publicView(r),
      sessionId: r.sessionId,
      talk: r.talk,
      executorCli: r.executor ? r.executor.cli : '',
      answer: r.result && r.result.answer ? r.result.answer.slice(0, 2000) : '',
      error: r.result ? r.result.error : '',
    }));
  }

  pendingSessionIds() {
    return [...new Set([...this.requests.values()].filter((r) => (r.state === 'open' || r.state === 'working') && r.sessionId).map((r) => r.sessionId))];
  }

  // 今日この依頼者が答えてもらった数（列の公平さの鍵に載せる。UTC の日付）
  served() {
    const day = new Date(this.now()).toISOString().slice(0, 10);
    if (this.servedToday.day !== day) this.servedToday = { day, count: 0 };
    return this.servedToday.count;
  }

  // ---- 執行者からの呼び出し（server が呼ぶ） -------------------------------------------

  claim(id, body = {}, remote = '') {
    const r = this.get(id);
    if (!r) return { status: 404, body: { error: 'その依頼はありません' } };
    const who = String(body.who || '').trim();
    if (!who) return { status: 400, body: { error: 'who が要ります' } };
    if (r.state !== 'open') return { status: 409, body: { error: '既に誰かが拾いました', state: r.state, executor: r.executor ? r.executor.node : '' } };
    r.state = 'working';
    r.executor = { node: who, address: String(remote || ''), port: Number(body.port) || 0, cli: String(body.cli || '') };
    r.claimed_at = nowIso();
    r.last_heartbeat = this.now();
    r.progress.push({ at: r.claimed_at, text: `引受 ${who}${r.executor.cli ? ` · ${r.executor.cli}` : ''}` });
    this.save();
    this.turnProgress(r, `引受 ${who}${r.executor.cli ? ` · ${r.executor.cli}` : ''}`);
    this.emit('changed');
    return { status: 200, body: { id: r.id, goal: r.goal, mode: r.mode, model: r.model, requires: r.requires, workspace: r.workspace, attachments: r.attachments.map((a) => a.name), priority: r.priority, posted_by: r.posted_by, posted_at: r.posted_at, title: r.title } };
  }

  heartbeat(id, body = {}, remote = '') {
    const r = this.get(id);
    if (!r) return { status: 404, body: { error: 'その依頼はありません' } };
    const who = String(body.who || '').trim();
    if (r.state === 'open' && !r.executor && who) {
      // 再起動のあと、執行者だけが覚えている。続きを認める
      r.state = 'working';
      r.executor = { node: who, address: String(remote || ''), port: Number(body.port) || 0, cli: String(body.cli || '') };
      r.claimed_at = r.claimed_at || nowIso();
    }
    if (r.state !== 'working' || !r.executor || r.executor.node !== who) {
      return { status: 409, body: { error: 'この依頼の執行者ではありません', state: r.state } };
    }
    r.last_heartbeat = this.now();
    if (body.screen != null) {
      const text = String(body.screen).slice(-MAX_SCREEN);
      this.screens.set(r.id, text);
      // 待っている会話の端末ミラーへ、執行者の画面をそのまま流す
      this.send('share:screen', { id: r.id, sessionId: r.sessionId, text, node: who, cli: r.executor ? r.executor.cli : '' });
    }
    if (body.progress) {
      const text = String(body.progress).slice(0, 200);
      r.progress.push({ at: nowIso(), text });
      if (r.progress.length > 50) r.progress.splice(0, r.progress.length - 50);
      this.turnProgress(r, text);
    }
    return { status: 200, body: { ok: true, state: r.state } };
  }

  result(id, body = {}) {
    const r = this.get(id);
    if (!r) return { status: 404, body: { error: 'その依頼はありません' } };
    const who = String(body.who || '').trim();
    if (r.state === 'done' || r.state === 'failed') return { status: 200, body: { ok: true, duplicate: true } };
    if (r.state === 'cancelled') return { status: 409, body: { error: '取り下げ済みです', state: r.state } };
    if (!r.executor || r.executor.node !== who) return { status: 409, body: { error: 'この依頼の執行者ではありません', state: r.state } };
    const status = body.status === 'done' ? 'done' : 'failed';
    let answer = String(body.answer || '');
    let error = String(body.error || '');
    if (answer.length > MAX_ANSWER) { answer = answer.slice(0, MAX_ANSWER); error = [error, '答えが長すぎたので末尾を切り詰めた'].filter(Boolean).join('\n'); }
    r.result = {
      status, answer, error, error_class: String(body.error_class || ''),
      agent_cli: String(body.agent_cli || r.executor.cli || ''), model: String(body.model || ''),
      elapsed_ms: Number(body.elapsed_ms) || 0, usage: body.usage && typeof body.usage === 'object' ? body.usage : null,
      branch: String(body.branch || ''), commit: String(body.commit || ''),
      resolved_by: who, resolved_at: nowIso(),
    };
    r.state = status;
    r.finished_at = r.result.resolved_at;
    this.screens.delete(r.id);
    if (status === 'done') { this.served(); this.servedToday.count += 1; }
    this.save();
    this.finish(r);
    this.emit('changed');
    return { status: 200, body: { ok: true } };
  }

  // ---- 終わり方 -------------------------------------------------------------------------

  finish(r) {
    const res = r.result;
    // 参加者側の枠切れ・一過性なら、1 回だけ黙って別の参加者へ
    if (res.status === 'failed' && RETRY_CLASSES.includes(res.error_class) && r.attempts < MAX_ATTEMPTS) {
      const onDone = this.done.get(r.id) || null;
      this.done.delete(r.id);
      this.turnProgress(r, `${res.resolved_by} は受けられなかった（${res.error_class}）。別の参加者へ再投函`);
      const again = this.post({ ...r, attachments: r.attachments, retryOf: r.id, attempts: r.attempts + 1, sessionId: r.sessionId }, { onDone });
      if (r.sessionId) { try { store.updateSession(this.userData, r.sessionId, { share: { id: again.id } }); } catch { /* 会話が消えていれば戻す先が無い */ } }
      return;
    }
    const message = this.assistantMessage(r);
    if (r.sessionId) {
      try {
        store.appendMessage(this.userData, r.sessionId, message);
        store.updateSession(this.userData, r.sessionId, { share: null });
      } catch (err) { message.error = `${message.error}\n保存できません: ${err.message}`.trim(); }
      this.send('turn:done', { id: r.sessionId, message });
    }
    this.release(r.id);
  }

  assistantMessage(r) {
    const res = r.result;
    const failed = res.status !== 'done';
    const who = r.executor ? r.executor.node : '';
    return {
      role: 'assistant', cli: res.agent_cli, model: res.model, policy: 'shared', tier: '',
      text: res.answer || (failed ? `（共有の依頼が失敗した: ${res.error || res.error_class || '理由不明'}）` : '（応答なし）'),
      code: failed ? 1 : 0, elapsedMs: res.elapsed_ms, stopped: false,
      parts: { thinking: [], information: [{ type: 'status', title: `共有 · ${who}${res.agent_cli ? ` · ${res.agent_cli}` : ''}`, status: failed ? 'error' : 'success', detail: `${Math.round((res.elapsed_ms || 0) / 1000)} 秒${r.attempts > 1 ? ` · ${r.attempts} 回目` : ''}` }] },
      error: failed ? (res.error || res.error_class) : '',
      share: { id: r.id, node: who, cli: res.agent_cli, branch: res.branch || '' },
    };
  }

  release(id) {
    const fn = this.done.get(id);
    this.done.delete(id);
    if (fn) { try { fn(); } catch { /* 解放は一度だけ */ } }
  }

  turnProgress(r, text) {
    if (r.sessionId) this.send('turn:progress', { id: r.sessionId, item: { text, status: 'running' } });
  }

  // ---- 利用者の操作 ----------------------------------------------------------------------

  async cancel(id, reason = '') {
    const r = this.get(id);
    if (!r || !(r.state === 'open' || r.state === 'working')) return false;
    const executor = r.executor;
    r.state = 'cancelled';
    r.finished_at = nowIso();
    r.result = { status: 'cancelled', answer: '', error: reason, error_class: '', agent_cli: executor ? executor.cli : '', model: '', elapsed_ms: 0, resolved_by: this.node, resolved_at: r.finished_at };
    this.save();
    if (executor && executor.address && executor.port) {
      await call({ address: executor.address, port: executor.port }, 'POST', `/requests/${encodeURIComponent(id)}/cancel`, { key: this.key, body: { who: this.node }, timeoutMs: 3000 }).catch(() => {});
    }
    if (r.sessionId) {
      const message = { role: 'assistant', cli: executor ? executor.cli : '', model: '', policy: 'shared', tier: '', text: '（取り下げた）', code: null, elapsedMs: 0, stopped: true, parts: { thinking: [], information: [] }, error: '', share: { id: r.id, node: executor ? executor.node : '', cli: executor ? executor.cli : '' } };
      try { store.appendMessage(this.userData, r.sessionId, message); store.updateSession(this.userData, r.sessionId, { share: null }); } catch { /* 会話が消えていれば戻す先が無い */ }
      this.send('turn:done', { id: r.sessionId, message });
    }
    this.release(id);
    this.emit('changed');
    return true;
  }

  cancelSession(sessionId) {
    const r = [...this.requests.values()].find((x) => x.sessionId === sessionId && (x.state === 'open' || x.state === 'working'));
    if (!r) return null;
    return this.cancel(r.id, '利用者が止めた');
  }

  setPriority(id, priority) {
    const r = this.get(id);
    if (!r || r.state !== 'open') return null;
    r.priority = priorityOf(priority);
    this.save();
    this.emit('changed');
    return this.publicView(r);
  }

  attachmentPath(id, name) {
    const r = this.get(id);
    if (!r) return '';
    const a = r.attachments.find((x) => x.name === String(name));
    return a && fs.existsSync(a.path) ? a.path : '';
  }

  // 心拍が途絶えた依頼を列へ戻す
  watchdog() {
    this.flushTalk().catch(() => {});
    let changed = false;
    for (const r of this.requests.values()) {
      if (r.state !== 'working') continue;
      if (this.now() - (r.last_heartbeat || 0) <= this.watchdogMs) continue;
      const who = r.executor ? r.executor.node : '';
      r.state = 'open';
      r.executor = null;
      r.claimed_at = '';
      r.progress.push({ at: nowIso(), text: `${who} の応答が途絶えた。列へ戻す` });
      this.turnProgress(r, `${who} の応答が途絶えた。列へ戻す`);
      this.notify(r.id).catch(() => {});
      changed = true;
    }
    if (changed) { this.save(); this.emit('changed'); }
    return changed;
  }

  // 終端した依頼は直近 50 件だけ残す
  prune() {
    const terminal = [...this.requests.values()].filter((r) => !(r.state === 'open' || r.state === 'working'))
      .sort((a, b) => String(b.finished_at).localeCompare(String(a.finished_at)));
    for (const r of terminal.slice(KEEP_TERMINAL)) { this.requests.delete(r.id); this.screens.delete(r.id); }
  }
}

module.exports = { Requester, newId, WATCHDOG_MS, TICK_MS, MAX_ATTEMPTS, MAX_ANSWER, MAX_SCREEN, MAX_SUMMARY, MAX_TALK, KEEP_TALK };
