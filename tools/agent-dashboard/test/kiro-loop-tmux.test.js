'use strict';

const assert = require('assert');

const tmux = require('../src/features/kiro-loop/main/tmux');
const exec = require('../src/features/kiro-loop/main/exec');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('pathDigest は sha1 先頭 8 桁（kiro-loop.py と同じ）', () => {
  const crypto = require('crypto');
  const expected = crypto.createHash('sha1').update('/home/me/proj').digest('hex').slice(0, 8);
  assert.strictEqual(tmux.pathDigest('/home/me/proj'), expected);
});

test('normalizeLinuxPath / wslPath は UNC を Linux パスへ', () => {
  assert.strictEqual(exec.wslPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo'), '/home/me/repo');
  assert.strictEqual(tmux.normalizeLinuxPath('\\\\wsl.localhost\\Ubuntu\\home\\me\\repo/'), '/home/me/repo');
});

test('listSessions は repo digest または cwd で絞る', () => {
  const repo = '/home/me/app';
  const digest = tmux.pathDigest(repo);
  const responses = [
    { ok: true, stdout: `kiro-loop-app-${digest}-abcd\nkiro-loop-other-ffffffff-zzzz\n`, stderr: '', error: '', status: 0 },
    { ok: true, stdout: '%1\t/home/me/app\tkiro\t1\n', stderr: '', error: '', status: 0 },
    { ok: true, stdout: '%2\t/home/me/other\tkiro\t1\n', stderr: '', error: '', status: 0 },
  ];
  let i = 0;
  const orig = exec.shInWsl;
  exec.shInWsl = () => responses[Math.min(i++, responses.length - 1)];
  try {
    const res = tmux.listSessions({ repo, prefix: 'kiro-loop-' });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.items.length, 1);
    assert.ok(res.items[0].session.includes(digest));
    assert.strictEqual(res.items[0].target, '%1');
  } finally {
    exec.shInWsl = orig;
  }
});

test('capture は target 必須', () => {
  const res = tmux.capture({ target: '' });
  assert.strictEqual(res.ok, false);
  assert.match(res.error, /target/);
});

test('feature preload が kiroLoop API を出す', () => {
  const { loadFeatures } = require('../src/features');
  const loop = loadFeatures().find((f) => f.id === 'kiro-loop');
  const api = loop.preloadApi();
  assert.strictEqual(typeof api.kiroLoopListSessions, 'function');
  assert.strictEqual(typeof api.kiroLoopCapture, 'function');
  const registered = [];
  loop.registerIpc({
    handle: (channel) => registered.push(channel),
    loadConfig: () => ({ kiroLoop: { sessionPrefix: 'kiro-loop-' } }),
    saveConfig: () => ({}),
  });
  assert.deepStrictEqual(registered.sort(), ['kiroLoop:capture', 'kiroLoop:listSessions'].sort());
  assert.ok(loop.configDefaults.kiroLoop);
});

console.log(`\n${passed} kiro-loop-tmux tests passed`);
