'use strict';
// テストの前にアプリをローカルで起動し、終わったら止める（webui-test.config.yaml の serve）。
// すでに URL が応答していれば起動せずにそれを使う（開発中に自分で立ち上げているとき）。

const http = require('http');
const https = require('https');
const { spawn, spawnSync } = require('child_process');

// 何か応答が返れば起動済みとみなす（404 や 302 でもサーバーは動いている）。
function reachable(url, timeoutMs = 2000) {
  return new Promise((resolve) => {
    let mod;
    try { mod = new URL(url).protocol === 'https:' ? https : http; } catch (_) { resolve(false); return; }
    const req = mod.get(url, { timeout: timeoutMs, rejectUnauthorized: false }, (res) => { res.resume(); resolve(true); });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

function stopTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    else process.kill(-child.pid, 'SIGTERM');
  } catch (_) {
    try { child.kill(); } catch (__) { /* 止まっている */ }
  }
}

// serve: { command, url, cwd, env, timeout }。戻り値: { started, url, stop() }
async function startServer(serve, { log = () => {} } = {}) {
  if (await reachable(serve.url)) {
    log(`起動済みのアプリを使います: ${serve.url}`);
    return { started: false, url: serve.url, stop: async () => {} };
  }
  log(`アプリを起動します: ${serve.command}（${serve.url} が応答するまで待つ）`);
  const child = spawn(serve.command, {
    cwd: serve.cwd,
    env: { ...process.env, ...serve.env },
    shell: true,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const tail = [];
  const keep = (d) => { tail.push(...String(d).split(/\r?\n/).filter(Boolean)); tail.splice(0, Math.max(0, tail.length - 15)); };
  child.stdout.on('data', keep);
  child.stderr.on('data', keep);
  let exited = null;
  child.on('exit', (code, signal) => { exited = signal || code; });
  child.on('error', (e) => { exited = e.message; });
  const deadline = Date.now() + serve.timeout;
  while (Date.now() < deadline) {
    if (exited !== null) break;
    if (await reachable(serve.url, 1000)) {
      return {
        started: true,
        url: serve.url,
        stop: async () => {
          stopTree(child);
          await new Promise((r) => { if (child.exitCode !== null || child.signalCode !== null) r(); else { child.once('exit', r); setTimeout(r, 5000); } });
        },
      };
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  stopTree(child);
  const why = exited !== null ? `起動のコマンドが終わってしまいました（${exited}）` : `${serve.timeout / 1000} 秒待っても ${serve.url} が応答しません`;
  throw new Error(`アプリを起動できません: ${why}\n  コマンド: ${serve.command}（${serve.cwd}）${tail.length ? '\n  ' + tail.join('\n  ') : ''}`);
}

// serve があれば起動してから fn を動かし、終わったら止める。
async function withServer(serve, fn, opts) {
  if (!serve) return fn();
  const server = await startServer(serve, opts);
  try {
    return await fn();
  } finally {
    await server.stop();
  }
}

module.exports = { startServer, withServer, reachable };
