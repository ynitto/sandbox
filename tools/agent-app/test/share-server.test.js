'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { download } = require('../src/main/share/server');

async function fixture(t, handler) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'share-download-'));
  const server = http.createServer(handler);
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(dir, { recursive: true, force: true });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { peer: { address: '127.0.0.1', port: server.address().port }, target: path.join(dir, 'attachment.txt') };
}

test('添付取得: 完全な応答だけを保存して成功する', async t => {
  const { peer, target } = await fixture(t, (_req, res) => res.end('添付の本文'));
  assert.equal(await download(peer, '/attachment', target, { key: 'secret' }), target);
  assert.equal(fs.readFileSync(target, 'utf8'), '添付の本文');
});

test('添付取得: 応答途中の切断は未処理エラーや待ち続けでなく失敗として返す', { timeout: 3000 }, async t => {
  const { peer, target } = await fixture(t, (_req, res) => {
    res.writeHead(200, { 'content-length': 100 });
    res.write('partial');
    setTimeout(() => res.destroy(), 20);
  });
  await assert.rejects(download(peer, '/attachment', target, { key: 'secret' }), /aborted|reset|closed/i);
});

test('添付取得: 保存先に書けなければ失敗として返す', async t => {
  const { peer, target } = await fixture(t, (_req, res) => res.end('body'));
  await assert.rejects(download(peer, '/attachment', path.join(target, 'missing', 'file'), { key: 'secret' }), { code: 'ENOENT' });
});
