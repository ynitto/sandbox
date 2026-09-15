"use strict";
// 会話の履歴を読む Python を、検索 1 回につき保存先ごとに 1 つだけ起こす。
// 常駐はさせない——検索が終わる・中止する・条件を打ち直すと必ず閉じる。
// やり取りは 1 行 1 要求の NDJSON で、途中経過（hit / progress）もそのまま流れてくる。
const fs = require('fs');
const path = require('path');
const host = require('./host');

const IDLE_TIMEOUT = 60000;
const MAX_LINE = 32 * 1024 * 1024;

class SessionWorker {
  constructor({ target, runtime, platform, spawnFn, onExit }) {
    this.target = target; this.runtime = runtime; this.platform = platform;
    this.spawn = spawnFn; this.onExit = onExit;
    this.pending = new Map(); this.sequence = 0; this.buffer = ''; this.closed = false;
    this.child = this.launch();
  }
  launch() {
    const runtime = this.platform === 'win32' && !this.target.native ? host.toWslPath(this.runtime) : this.runtime;
    const script = 'import sys,runpy;sys.path.insert(0,' + JSON.stringify(runtime) + ');runpy.run_module("agent_audit.session_browser",run_name="__main__")';
    const command = 'exec python3 -c ' + host.sq(script);
    const argv = this.platform === 'win32' ? ['-d', this.target.distro, '-e', 'bash', '-lc', command] : ['-lc', command];
    const child = this.spawn(this.target.native ? 'python' : this.platform === 'win32' ? 'wsl.exe' : '/bin/bash',
      this.target.native ? ['-c', script] : argv, { windowsHide: true });
    child.stdout.on('data', bytes => this.receive(bytes.toString()));
    child.stderr.on('data', bytes => { this.error = ((this.error || '') + bytes.toString()).slice(-2000); });
    child.stdin.on('error', () => {});
    child.on('error', err => this.fail(err));
    child.on('close', () => this.fail(new Error('履歴を取得できません。Python 3 の利用環境を確認してください。' + (this.error || ''))));
    return child;
  }
  receive(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > MAX_LINE) return this.fail(new Error('履歴の取得が上限に達しました。条件を絞るか会話を取り込んでください'));
    let index;
    while ((index = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, index); this.buffer = this.buffer.slice(index + 1);
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      const job = this.pending.get(message.id);
      if (!job) continue;
      job.touch();
      if (message.ok === true) { this.pending.delete(message.id); job.resolve(message.data); }
      else if (message.ok === false) { this.pending.delete(message.id); job.reject(new Error(message.error || '履歴を取得できません')); }
      else job.onEvent(message);
    }
  }
  fail(err) {
    this.closed = true;
    for (const [id, job] of this.pending) { this.pending.delete(id); job.reject(err); }
    this.onExit?.(this);
  }
  request(payload, onEvent = () => {}) {
    if (this.closed) return Promise.reject(new Error('履歴の取得が終了しました'));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      let timer;
      const done = fn => value => { clearTimeout(timer); fn(value); };
      // 応答が全く来ないまま止まったワーカーだけを切る。途中経過が届く限り待つ。
      const touch = () => { clearTimeout(timer); timer = setTimeout(() => {
        this.pending.delete(id); this.kill(); reject(new Error('履歴の取得に時間がかかりすぎました'));
      }, IDLE_TIMEOUT); };
      this.pending.set(id, { resolve: done(resolve), reject: done(reject), onEvent, touch });
      touch();
      this.child.stdin.write(JSON.stringify({ ...payload, id }) + '\n');
    });
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    try { this.child.stdin.end(); } catch { /* already gone */ }
    // 終わりを待たずに帰す。読み込み中のものがあれば次の tick で落ちる。
    setTimeout(() => { try { this.child.kill(); } catch { /* already gone */ } }, 1000).unref?.();
  }
  kill() {
    this.closed = true;
    try { this.child.kill(); } catch { /* already gone */ }
  }
}

// 1 回の検索が使うワーカーの束。検索が終われば close() で全部閉じる。
class WorkerGroup {
  constructor(factory) { this.factory = factory; this.workers = new Map(); this.closed = false; }
  get(target) {
    if (this.closed) throw new Error('検索を中止しました');
    if (!this.workers.has(target.id)) this.workers.set(target.id, this.factory(target));
    return this.workers.get(target.id);
  }
  close() {
    this.closed = true;
    for (const worker of this.workers.values()) worker.close();
    this.workers.clear();
  }
  kill() {
    this.closed = true;
    for (const worker of this.workers.values()) worker.kill();
    this.workers.clear();
  }
}

function runtimeDir(resourcesPath) {
  const packaged = resourcesPath && path.join(resourcesPath, 'audit-runtime');
  return packaged && fs.existsSync(packaged) ? packaged : path.resolve(__dirname, '../../../agent-audit');
}

module.exports = { SessionWorker, WorkerGroup, runtimeDir };
