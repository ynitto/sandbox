'use strict';

// gitlab-review-viewer が既に起動しているときに、ローカル IPC でディープリンクを
// 即時ハンドオフするクライアント側。exe を spawn しないので portable exe の自己展開
// コスト（数秒）を回避できる。起動していなければ接続に失敗し false を返すので、
// 呼び出し側（review.js）が従来どおり exe 起動へフォールバックする。
//
// エンドポイント名は gitlab-review-viewer 側（src/main/handoff.js の endpointPath）と
// **同一の決定的導出**でなければならない（この 2 箇所は対で保守すること）。
// electron に依存しないモジュールに切り出し、単体テスト（round-trip）できるようにしている。

const net = require('net');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

function endpointPath() {
  const key = crypto
    .createHash('sha1')
    .update(`gitlab-review-viewer::${os.userInfo().username || ''}`)
    .digest('hex')
    .slice(0, 12);
  return process.platform === 'win32'
    ? `\\\\.\\pipe\\gitlab-review-viewer-${key}`
    : path.join(os.tmpdir(), `gitlab-review-viewer-${key}.sock`);
}

// 起動済み gitlab-review-viewer へ URL を渡す。成功なら true（exe 起動不要）。
// 未起動（ENOENT/ECONNREFUSED）・応答なし（timeout）なら false（呼び出し側が exe 起動へ）。
function tryHandoff(url, timeoutMs = 400) {
  return new Promise((resolve) => {
    let done = false;
    let ack = '';
    const sock = net.connect(endpointPath());
    const finish = (ok) => {
      if (done) return;
      done = true;
      try {
        sock.destroy();
      } catch {
        /* no-op */
      }
      resolve(ok);
    };
    sock.setEncoding('utf8');
    sock.setTimeout(timeoutMs);
    sock.on('connect', () => sock.write(`${JSON.stringify({ url })}\n`));
    sock.on('data', (d) => {
      ack += d;
      if (ack.includes('ok')) finish(true);
    });
    sock.on('timeout', () => finish(false));
    sock.on('error', () => finish(false)); // 未起動なら即ここ（接続拒否）
    sock.on('close', () => finish(ack.includes('ok')));
  });
}

module.exports = { endpointPath, tryHandoff };
