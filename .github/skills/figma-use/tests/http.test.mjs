import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { ApiError, EXIT, FigmaClient, parseRetryAfter } from '../scripts/lib/http.mjs';

function fakeFetch(responses, calls = []) {
  return async (url, init) => {
    calls.push({ url: String(url), headers: init.headers });
    const r = responses.shift();
    if (r instanceof Error) throw r;
    return new Response(r.body ?? '{}', { status: r.status ?? 200, headers: r.headers ?? {} });
  };
}

function client(responses, extra = {}) {
  const calls = [];
  const logs = [];
  const c = new FigmaClient({ token: 'secret-token', apiBase: 'https://api.figma.com', backoffMs: 1, fetchImpl: fakeFetch(responses, calls), log: (m) => logs.push(m), ...extra });
  return { c, calls, logs };
}

test('Retry-After は秒と HTTP 日付を読む', () => {
  assert.equal(parseRetryAfter('3'), 3000);
  assert.equal(parseRetryAfter(new Date(Date.now() + 2000).toUTCString(), Date.now()) > 0, true);
  assert.equal(parseRetryAfter('abc'), null);
});

test('429 は Retry-After だけ待って再試行する', async () => {
  const { c, calls, logs } = client([{ status: 429, headers: { 'retry-after': '0' } }, { body: '{"ok":true}' }]);
  const r = await c.getJson('/v1/files/k');
  assert.deepEqual(r.data, { ok: true });
  assert.equal(calls.length, 2);
  assert.ok(logs.some((l) => l.includes('HTTP 429')));
});

test('Retry-After が長すぎるときは待たずにレート制限として止める', async () => {
  const { c } = client([{ status: 429, headers: { 'retry-after': '3600', 'x-figma-plan-tier': 'pro', 'x-figma-rate-limit-type': 'low' } }], { maxRetryWaitMs: 1000 });
  await assert.rejects(c.getJson('/v1/files/k'), (e) => e instanceof ApiError && e.exitCode === EXIT.RATE_LIMIT && /Collab/.test(e.hint));
});

test('5xx と通信エラーは有限回だけ再試行する', async () => {
  const { c, calls } = client([{ status: 500 }, new TypeError('fetch failed'), { status: 502 }, { status: 503 }], { maxRetries: 3 });
  await assert.rejects(c.getJson('/v1/files/k'), (e) => e.status === 503 && e.exitCode === EXIT.NETWORK);
  assert.equal(calls.length, 4);
});

test('401・403・404・400 は再試行せず、種類ごとに終了コードと対処を返す', async () => {
  for (const [status, code, hint] of [[401, EXIT.AUTH, /作り直し/], [403, EXIT.AUTH, /スコープ/], [404, EXIT.NOT_FOUND, /file key/], [400, EXIT.ERROR, /depth/]]) {
    const { c, calls } = client([{ status, body: JSON.stringify({ status, err: 'x' }) }]);
    await assert.rejects(c.getJson('/v1/files/k'), (e) => e.status === status && e.exitCode === code && hint.test(e.hint));
    assert.equal(calls.length, 1);
  }
});

test('タイムアウトを通信エラーとして扱う', async () => {
  const { c } = client([Object.assign(new Error('timeout'), { name: 'TimeoutError' })], { maxRetries: 0, timeoutMs: 5 });
  await assert.rejects(c.getJson('/v1/files/k'), (e) => e.exitCode === EXIT.NETWORK && /タイムアウト/.test(e.message));
});

test('キャッシュがあれば API を呼ばず、取得日時と version を知らせる。--refresh なら取り直す', async () => {
  const cacheDir = fs.mkdtempSync(path.join(os.tmpdir(), 'figma-use-cache-'));
  const first = client([{ body: '{"version":"42"}' }], { cacheDir });
  await first.c.getJson('/v1/files/k/nodes', { ids: '1:2' });
  const second = client([], { cacheDir });
  const r = await second.c.getJson('/v1/files/k/nodes', { ids: '1:2' });
  assert.equal(r.fromCache, true);
  assert.equal(second.calls.length, 0);
  assert.ok(second.logs.some((l) => l.includes('version 42') && l.includes('--refresh')));
  const third = client([{ body: '{"version":"43"}' }], { cacheDir, refresh: true });
  assert.equal((await third.c.getJson('/v1/files/k/nodes', { ids: '1:2' })).data.version, '43');
  assert.equal(third.calls.length, 1);
  assert.ok(!fs.readFileSync(path.join(cacheDir, 'api', fs.readdirSync(path.join(cacheDir, 'api'))[0]), 'utf8').includes('secret-token'));
});

test('画像のダウンロードにはトークンを付けず、https 以外は拒否する', async () => {
  const { c, calls } = client([{ body: 'PNGDATA' }]);
  await c.download('https://s3.example.com/img.png');
  assert.equal(calls[0].headers['X-Figma-Token'], undefined);
  await assert.rejects(c.download('http://s3.example.com/img.png'), /https 以外/);
  await assert.rejects(c.download('file:///etc/passwd'), /https 以外/);
});

test('トークンが無ければ API を呼ばずに認証エラーにする', async () => {
  const { c, calls } = client([], { token: null });
  await assert.rejects(c.getJson('/v1/files/k'), (e) => e.exitCode === EXIT.AUTH);
  assert.equal(calls.length, 0);
});
