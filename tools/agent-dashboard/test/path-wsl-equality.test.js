'use strict';

// Windows ビュアー × WSL 本体でパス規約が食い違っても照合できることを固定する。
// 追加依存なしで `node test/path-wsl-equality.test.js`。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');

const { _pathKey, pathsEqual, hostsMatch, sameMachineStatus, projectLiveness } = project;

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('WSL UNC（wsl.localhost）と Linux パスが同じキーになる', () => {
  assert.strictEqual(
    _pathKey('\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project'),
    '/home/me/webapp-agent-state/.agent-project'
  );
  assert.ok(pathsEqual(
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project',
    '/home/me/webapp-agent-state/.agent-project'
  ));
});

test('wsl$ と wsl.localhost を同一視する', () => {
  assert.ok(pathsEqual(
    '\\\\wsl$\\Ubuntu\\home\\me\\webapp\\.agent-project',
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project'
  ));
});

test('win32 path.resolve 残骸（\\home\\...）も Linux パスと一致', () => {
  assert.ok(pathsEqual('\\home\\me\\webapp\\.agent-project', '/home/me/webapp/.agent-project'));
  assert.ok(pathsEqual('C:\\home\\me\\webapp\\.agent-project', '/home/me/webapp/.agent-project'));
});

test('スラッシュ混在 UNC も正規化できる', () => {
  assert.ok(pathsEqual(
    '//wsl.localhost/Ubuntu/home/me/webapp/.agent-project',
    '/home/me/webapp/.agent-project'
  ));
});

test('/mnt/<drive> と Windows ドライブ表記が同じキーになる', () => {
  assert.strictEqual(_pathKey('/mnt/c/Users/me/proj/.agent-project'), 'c:/users/me/proj/.agent-project');
  assert.ok(pathsEqual('/mnt/c/Users/me/proj', '/mnt/C/users/ME/proj'));
  // UNC 経由の /mnt/c/... も同じキーに寄る
  assert.ok(pathsEqual(
    '\\\\wsl.localhost\\Ubuntu\\mnt\\c\\Users\\me\\proj',
    '/mnt/c/Users/me/proj'
  ));
});

test('異なるディストロの同名パスは同一視しない', () => {
  assert.ok(!pathsEqual(
    '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project',
    '\\\\wsl.localhost\\Debian\\home\\me\\webapp\\.agent-project'
  ));
  // 片方が Linux パス（ディストロ情報なし）なら従来どおり一致を許す
  assert.ok(pathsEqual(
    '\\\\wsl.localhost\\Debian\\home\\me\\webapp\\.agent-project',
    '/home/me/webapp/.agent-project'
  ));
});

test('hostsMatch は大小・DNS サフィックス差を吸収', () => {
  assert.ok(hostsMatch('MyPC', 'mypc'));
  assert.ok(hostsMatch('mypc.localdomain', 'mypc'));
  assert.ok(!hostsMatch('alpha', 'beta'));
  assert.ok(!hostsMatch('', 'mypc'));
});

test('sameMachineStatus: runtime=wsl は win32 で同一マシン', () => {
  if (process.platform === 'win32') {
    assert.ok(sameMachineStatus({ host: 'other-box', runtime: 'wsl' }));
  } else {
    // Linux では hostname 不一致なら同一マシン扱いにしない（runtime だけでは不足）。
    // 別ホストは前置で作る: 後置だとホスト名にドットを含む環境（macOS の `foo.local`）で
    // 短縮名が変わらず、DNS サフィックス差を吸収する hostsMatch が一致と判定する。
    assert.ok(!sameMachineStatus({ host: `x-${os.hostname()}`, runtime: 'wsl' }));
  }
  assert.ok(sameMachineStatus({ host: os.hostname(), runtime: 'linux' }));
});

test('projectLiveness: effective_root_windows（状態 worktree UNC）で instances 一致', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-wsl-live-'));
  const idir = path.join(os.homedir(), '.agent-project', 'instances');
  fs.mkdirSync(idir, { recursive: true });
  const file = path.join(idir, `kpv-wsl-eq-${process.pid}.json`);
  const uncState = '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project';
  fs.writeFileSync(file, JSON.stringify({
    pid: process.pid,
    root: '/home/me/webapp/.agent-project',
    root_windows: '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp\\.agent-project',
    effective_root: '/home/me/webapp-agent-state/.agent-project',
    effective_root_windows: uncState,
    backlog: '/home/me/webapp-agent-state/.agent-project/backlog',
    heartbeat: Date.now() / 1000,
    ttl: 90,
    host: 'wsl-host',
    runtime: 'wsl',
  }));
  try {
    // ビュアーが状態 worktree の UNC を開いている想定
    const live = projectLiveness(uncState);
    assert.strictEqual(live.running, true, JSON.stringify(live));
    assert.strictEqual(live.via, 'instances');
    // Linux 表記で照合しても同じ
    assert.strictEqual(projectLiveness('/home/me/webapp-agent-state/.agent-project').via, 'instances');
  } finally {
    fs.unlinkSync(file);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('projectLiveness: status.json の runtime=wsl は win32 で status-local', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-wsl-st-'));
  fs.writeFileSync(path.join(dir, 'status.json'), JSON.stringify({
    // 前置で別ホストにする（後置はドット入りホスト名で短縮名が変わらない。上の注記参照）
    host: `wsl-distinct-${os.hostname()}`,
    runtime: 'wsl',
    watch: true,
    paused: false,
    updated_iso: new Date().toISOString().replace('T', ' ').slice(0, 19),
    fresh_after_sec: 600,
  }));
  try {
    const live = projectLiveness(dir);
    if (process.platform === 'win32') {
      assert.strictEqual(live.via, 'status-local', JSON.stringify(live));
    } else {
      // Linux 上では runtime=wsl だけでは同一マシンにしない（ホスト名不一致 → sync）
      assert.strictEqual(live.via, 'status-sync', JSON.stringify(live));
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
