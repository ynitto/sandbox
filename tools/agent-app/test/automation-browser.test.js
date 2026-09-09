'use strict';

// ブラウザの見本のために、この端末で Edge（無ければ Chrome）をリモートデバッグ付きで起こす。
// 起動と応答の確認は差し替えた関数で確かめる（実際のブラウザは起こさない）。

const { test } = require('node:test');
const assert = require('node:assert');
const browser = require('../src/main/automation/browser');

const WIN_ENV = { 'ProgramFiles(x86)': 'C:\\PF86', ProgramFiles: 'C:\\PF', LOCALAPPDATA: 'C:\\Users\\me\\AppData\\Local' };

test('Windows では既定の置き場から Edge を先に、無ければ Chrome を探す', () => {
  const edge = 'C:\\PF86\\Microsoft\\Edge\\Application\\msedge.exe';
  const chrome = 'C:\\PF\\Google\\Chrome\\Application\\chrome.exe';
  assert.strictEqual(browser.findBrowser({ platform: 'win32', env: WIN_ENV, exists: (f) => f === edge || f === chrome }), edge);
  assert.strictEqual(browser.findBrowser({ platform: 'win32', env: WIN_ENV, exists: (f) => f === chrome }), chrome);
  assert.strictEqual(browser.findBrowser({ platform: 'win32', env: WIN_ENV, exists: () => false }), '');
  assert.strictEqual(browser.browserLabel(edge), 'Edge');
  assert.strictEqual(browser.browserLabel(chrome), 'Chrome');
});

test('Linux では PATH で探す（resolvePath を使う）', () => {
  const resolvePath = (name) => (name === 'google-chrome' ? '/usr/bin/google-chrome' : '');
  assert.strictEqual(browser.findBrowser({ platform: 'linux', exists: () => false, resolvePath }), '/usr/bin/google-chrome');
  assert.strictEqual(browser.findBrowser({ platform: 'linux', exists: () => false, resolvePath: () => '' }), '');
});

test('起動の引数はリモートデバッグのポートと記録専用のプロファイルを必ず持つ', () => {
  assert.deepStrictEqual(browser.launchArgs({ port: 9222, profileDir: 'C:\\ud\\recording-browser-profile', url: 'https://a.test/?x=1&y=2' }), [
    '--remote-debugging-port=9222', '--user-data-dir=C:\\ud\\recording-browser-profile', '--no-first-run', '--no-default-browser-check', 'https://a.test/?x=1&y=2',
  ]);
  assert.strictEqual(browser.launchArgs({ profileDir: '/p' }).at(-1), 'about:blank');
  assert.strictEqual(browser.endpointFor(9222), 'http://localhost:9222');
  assert.strictEqual(browser.PORT, 9222);
});

function harness({ alive = [false, false, true], spawnError = null } = {}) {
  const calls = { spawn: [], probes: 0 };
  const probe = async () => { const ok = alive[Math.min(calls.probes, alive.length - 1)]; calls.probes += 1; return { ok, browser: ok ? 'Edg/130' : '' }; };
  const spawn = (file, args, opts) => {
    if (spawnError) throw spawnError;
    calls.spawn.push({ file, args, opts });
    return { unref() { calls.unref = true; }, on() {} };
  };
  return { calls, probe, spawn };
}

test('起こしてから /json/version に答えが出るまで待ち、接続先と起動したブラウザを返す', async () => {
  const h = harness();
  const edge = 'C:\\PF86\\Microsoft\\Edge\\Application\\msedge.exe';
  const res = await browser.launchRecordingBrowser({
    url: 'https://a.test/list', profileDir: 'C:\\ud\\profile', platform: 'win32', env: WIN_ENV, exists: (f) => f === edge,
    spawn: h.spawn, probe: h.probe, sleep: async () => {}, mkdir: () => {},
  });
  assert.deepStrictEqual(res, { ok: true, browser: 'Edge', file: edge, version: 'Edg/130', port: 9222, endpoint: 'http://localhost:9222', url: 'https://a.test/list', reused: false });
  assert.strictEqual(h.calls.spawn.length, 1);
  assert.strictEqual(h.calls.spawn[0].file, edge);
  assert.ok(h.calls.spawn[0].args.includes('--remote-debugging-port=9222'));
  assert.ok(h.calls.spawn[0].args.includes('--user-data-dir=C:\\ud\\profile'));
  assert.strictEqual(h.calls.spawn[0].opts.detached, true, '待たずに切り離す');
  assert.strictEqual(h.calls.unref, true);
  assert.strictEqual(h.calls.probes, 3, '起動前に 1 回、起動後は答えが出るまで');
});

test('既に応答があるポートでも起動はする（同じプロファイルなら既存の窓にタブが開く）が、待たない', async () => {
  const h = harness({ alive: [true] });
  const res = await browser.launchRecordingBrowser({
    profileDir: '/p', platform: 'linux', exists: () => false, resolvePath: (n) => (n === 'microsoft-edge' ? '/usr/bin/microsoft-edge' : ''),
    spawn: h.spawn, probe: h.probe, sleep: async () => {}, mkdir: () => {},
  });
  assert.strictEqual(res.reused, true);
  assert.strictEqual(res.browser, 'Edge');
  assert.strictEqual(h.calls.spawn.length, 1);
  assert.strictEqual(h.calls.probes, 1);
});

test('ブラウザが無い・URL が不正・応答が出ない・起動に失敗したときは理由を言って止める', async () => {
  const h = harness();
  const base = { profileDir: '/p', platform: 'linux', exists: () => false, resolvePath: () => '/usr/bin/microsoft-edge', spawn: h.spawn, probe: h.probe, sleep: async () => {}, mkdir: () => {} };
  await assert.rejects(browser.launchRecordingBrowser({ ...base, resolvePath: () => '' }), /Edge か Chrome）が見つかりません/);
  await assert.rejects(browser.launchRecordingBrowser({ ...base, profileDir: '' }), /プロファイルの置き場/);
  await assert.rejects(browser.launchRecordingBrowser({ ...base, url: 'ftp://x' }), /http:\/\/ か https:\/\//);
  const never = harness({ alive: [false] });
  let now = 0;
  const realNow = Date.now;
  Date.now = () => now;
  try {
    await assert.rejects(browser.launchRecordingBrowser({ ...base, spawn: never.spawn, probe: never.probe, sleep: async () => { now += 1000; }, timeoutMs: 3000 }), /ポート 9222）に応答しません/);
  } finally { Date.now = realNow; }
  const broken = harness({ spawnError: new Error('EACCES') });
  await assert.rejects(browser.launchRecordingBrowser({ ...base, spawn: broken.spawn, probe: broken.probe }), /ブラウザを起動できませんでした: EACCES/);
});
