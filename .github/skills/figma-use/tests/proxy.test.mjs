import assert from 'node:assert/strict';
import { test } from 'node:test';
import { childEnv, describeProxy, hasProxy, readProxyEnv } from '../scripts/modules/proxy.mjs';

test('http_proxy / https_proxy / no_proxy を大文字小文字を問わず読む', () => {
  assert.deepEqual(readProxyEnv({ https_proxy: 'http://a:1', Http_Proxy: 'http://b:2', No_Proxy: 'localhost' }), {
    HTTPS_PROXY: 'http://a:1', HTTP_PROXY: 'http://b:2', NO_PROXY: 'localhost',
  });
  assert.equal(hasProxy({ hTTPs_pRoXy: 'http://a:1' }), true);
  assert.equal(hasProxy({ NO_PROXY: 'x' }), false);
  assert.equal(hasProxy({ https_proxy: '' }), false);
});

test('同じ名前が複数の綴りにあれば大文字を優先する', () => {
  assert.equal(readProxyEnv({ https_proxy: 'http://lower:1', HTTPS_PROXY: 'http://upper:2' }).HTTPS_PROXY, 'http://upper:2');
});

test('子プロセスの環境は綴りを大文字にそろえ、NODE_USE_ENV_PROXY を付ける', () => {
  const env = childEnv({ PATH: '/bin', Https_Proxy: 'http://a:1', http_proxy: 'http://b:2' });
  assert.deepEqual(Object.keys(env).filter((k) => /proxy/i.test(k)).sort(), ['FIGMA_USE_PROXY_CHILD', 'HTTPS_PROXY', 'HTTP_PROXY', 'NODE_USE_ENV_PROXY']);
  assert.equal(env.NODE_USE_ENV_PROXY, '1');
  assert.equal(env.PATH, '/bin');
});

test('表示ではプロキシの認証情報を伏せる', () => {
  assert.equal(describeProxy('http://user:secret@proxy.example.com:8080'), 'http://proxy.example.com:8080');
  assert.doesNotMatch(describeProxy('not a url'), /not a url/);
});
