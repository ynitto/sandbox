'use strict';

// 起動済み gitlab-review-viewer へのローカル IPC ハンドオフの往復テスト。
// クライアント（この viewer の reviewHandoff）とサーバ（gitlab-review-viewer の handoff）を
// 実際のソケットでつなぎ、(1) 両者のエンドポイント導出が一致、(2) URL が届いて ok が返る、
// (3) 未起動なら false（exe 起動へフォールバック）を検証する。electron 非依存で動く。

const assert = require('assert');
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

(async () => {
  await test('両側のエンドポイント導出は一致する（同一ユーザー・決定的）', () => {
    assert.strictEqual(client.endpointPath(), server.endpointPath());
  });

  await test('起動済みなら URL がサーバへ届き ok が返る（exe を spawn しない）', async () => {
    let received = null;
    const stop = server.startHandoffServer((url) => {
      received = url;
    });
    try {
      // listen が確立するまで少し待つ
      await new Promise((r) => setTimeout(r, 100));
      const url = 'gitlab-review-viewer://open?url=https%3A%2F%2Fgl.example%2Fg%2Fp%2F-%2Fissues%2F7';
      const ok = await client.tryHandoff(url, 800);
      assert.strictEqual(ok, true, 'ハンドオフは成功する');
      assert.strictEqual(received, url, 'サーバは同じ URL を受け取る');
    } finally {
      stop();
    }
  });

  await test('未起動なら false（接続失敗 → 呼び出し側が exe 起動へフォールバック）', async () => {
    // サーバを起動していない状態。接続は即失敗（ENOENT/ECONNREFUSED）するはず。
    const ok = await client.tryHandoff('gitlab-review-viewer://open?url=x', 800);
    assert.strictEqual(ok, false);
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
