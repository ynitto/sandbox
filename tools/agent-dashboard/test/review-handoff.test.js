'use strict';

// 起動済み gitlab-review-viewer へのローカル IPC ハンドオフの往復テスト。
// クライアント（この viewer の reviewHandoff）とサーバ（gitlab-review-viewer の handoff）を
// インメモリのソケット対でつなぎ、(1) 両者のエンドポイント導出が一致、(2) URL が届いて ok が返る、
// (3) 未起動なら false（exe 起動へフォールバック）を検証する。OS の IPC 権限には依存しない。

const assert = require('assert');
const { EventEmitter } = require('events');
const client = require('../src/main/reviewHandoff');
// 姉妹アプリ（同一リポジトリ）のサーバ実装。契約が両側で噛み合うことを end-to-end で確かめる。
const server = require('../../gitlab-review-viewer/src/main/handoff');

let passed = 0;
function test(name, fn) {
  return Promise.resolve(fn()).then(() => {
    passed += 1;
    console.log(`ok - ${name}`);
  });
}

function socketPair() {
  class MemorySocket extends EventEmitter {
    setEncoding() {}
    setTimeout() {}
    write(data) { queueMicrotask(() => this.peer.emit('data', data)); }
    end() { queueMicrotask(() => { this.emit('close'); this.peer.emit('close'); }); }
    destroy() {}
  }
  const clientSocket = new MemorySocket();
  const serverSocket = new MemorySocket();
  clientSocket.peer = serverSocket;
  serverSocket.peer = clientSocket;
  return { clientSocket, serverSocket };
}

(async () => {
  await test('両側のエンドポイント導出は一致する（同一ユーザー・決定的）', () => {
    assert.strictEqual(client.endpointPath(), server.endpointPath());
  });

  await test('起動済みなら URL がサーバへ届き ok が返る（exe を spawn しない）', async () => {
    let received = null;
    const { clientSocket, serverSocket } = socketPair();
    server.acceptHandoffConnection(serverSocket, (url) => {
      received = url;
    });
    const connect = () => {
      queueMicrotask(() => clientSocket.emit('connect'));
      return clientSocket;
    };
    const url = 'gitlab-review-viewer://open?url=https%3A%2F%2Fgl.example%2Fg%2Fp%2F-%2Fissues%2F7';
    const ok = await client.tryHandoff(url, 800, connect);
    assert.strictEqual(ok, true, 'ハンドオフは成功する');
    assert.strictEqual(received, url, 'サーバは同じ URL を受け取る');
  });

  await test('未起動なら false（接続失敗 → 呼び出し側が exe 起動へフォールバック）', async () => {
    const connect = () => {
      const socket = new EventEmitter();
      socket.setEncoding = socket.setTimeout = socket.destroy = () => {};
      queueMicrotask(() => socket.emit('error', new Error('not listening')));
      return socket;
    };
    const ok = await client.tryHandoff('gitlab-review-viewer://open?url=x', 800, connect);
    assert.strictEqual(ok, false);
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
