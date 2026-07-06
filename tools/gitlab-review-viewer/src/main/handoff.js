'use strict';

// 既に起動しているインスタンスへディープリンクを即時ハンドオフするローカル IPC
// エンドポイント（サーバ側）。
//
// portable exe を「連携起動」しようとすると、既に起動済みでも OS は毎回
//   自己展開（一時ディレクトリへ）→ Electron 起動 → single-instance で argv 転送 → 即終了
// という 2 個目プロセスの立ち上げコスト（数秒）を必ず払う。起動済みなら exe を spawn せず、
// このローカルソケット（Windows: 名前付きパイプ／その他: Unix ドメインソケット）へ URL を
// 送るだけで、そのウィンドウが即座に対象を開ける。
//
// エンドポイント名は kiro-projects-viewer 側（src/main/reviewHandoff.js の endpointPath）と
// **同一の決定的導出**でなければならない（ユーザーごとに分離するため username の sha1 を混ぜる）。
// この 2 箇所は対で保守すること。プロトコルは 1 行 =「生の URL か {"url":...} を改行終端」。
// サーバは 'ok' を返す。ローカルユーザー限定のソケットで、扱う URL は
// gitlab-review-viewer:// のみ（呼び出し側で prefix 検証する）。

const net = require('net');
const os = require('os');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const MAX_MSG = 8 * 1024; // 受信メッセージ上限（ディープリンク 1 本で十分）

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

// onUrl(url) は受け取った 1 本の URL ごとに呼ばれる（prefix 検証・ウィンドウ転送は呼び出し側）。
// 戻り値は停止関数（アプリ終了時に呼ぶ）。listen 失敗（権限等）でもアプリは動かし続ける
// （その場合は従来どおり argv 経由の連携起動にフォールバックされる）。
function startHandoffServer(onUrl) {
  const endpoint = endpointPath();
  // Unix ドメインソケットはクラッシュ残骸が残ることがある。single-instance ロック取得済み＝
  // 自分が唯一なので、残骸を unlink してから listen する（Windows パイプは所有プロセス終了で
  // OS が自動解放するため不要）。
  if (process.platform !== 'win32') {
    try {
      fs.unlinkSync(endpoint);
    } catch {
      /* 無ければ無視 */
    }
  }

  const server = net.createServer((sock) => {
    let buf = '';
    sock.setEncoding('utf8');
    const onData = (d) => {
      buf += d;
      if (buf.length > MAX_MSG) {
        sock.destroy();
        return;
      }
      const nl = buf.indexOf('\n');
      if (nl === -1) return; // 行が揃うまで待つ
      sock.removeListener('data', onData);
      const line = buf.slice(0, nl).trim();
      let url = '';
      if (line.startsWith('{')) {
        try {
          url = String(JSON.parse(line).url || '');
        } catch {
          url = '';
        }
      } else {
        url = line;
      }
      if (url) {
        try {
          sock.write('ok');
        } catch {
          /* 応答は best-effort */
        }
        onUrl(url);
      }
      sock.end();
    };
    sock.on('data', onData);
    sock.on('error', () => {}); // 相手が即切断してもクラッシュさせない
  });
  server.on('error', () => {}); // listen 失敗でもアプリは動かす
  try {
    server.listen(endpoint);
  } catch {
    /* 失敗時は従来の argv 経路が使われる */
  }

  return function stop() {
    try {
      server.close();
    } catch {
      /* no-op */
    }
    if (process.platform !== 'win32') {
      try {
        fs.unlinkSync(endpoint);
      } catch {
        /* no-op */
      }
    }
  };
}

module.exports = { startHandoffServer, endpointPath };
