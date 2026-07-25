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

test('/mnt/<drive> は Windows ドライブへ寄せない（経路ごと廃止・W2-4）', () => {
  // 以前は /mnt/c/... を C:\... と同一視していた。dashboard が Windows 側のパスで
  // プロジェクトを掴む経路が無くなったので、POSIX パスは POSIX のまま比較する。
  assert.strictEqual(_pathKey('/mnt/c/Users/me/proj/.agent-project'), '/mnt/c/users/me/proj/.agent-project');
  assert.ok(pathsEqual('/mnt/c/Users/me/proj', '/mnt/C/users/ME/proj'));
  assert.ok(!pathsEqual('/mnt/c/Users/me/proj', 'C:\\Users\\me\\proj'));
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

test('projectLiveness: 監督下の子は UNC / POSIX どちらの表記でも確定判定になる', () => {
  // Windows のビュアーは UNC、実行側（WSL）は POSIX を書く。根拠を engine の
  // children[].alive へ移した後も、表記差で確定判定を取りこぼさないこと（実装計画 W1-9）。
  const uncState = '\\\\wsl.localhost\\Ubuntu\\home\\me\\webapp-agent-state\\.agent-project';
  const child = { name: 'webapp', alive: true, quarantined: false, paused: false,
                  root: '/home/me/webapp-agent-state/.agent-project' };
  assert.strictEqual(projectLiveness(uncState, child).via, 'engine');
  assert.strictEqual(projectLiveness(uncState, child).running, true);
  assert.strictEqual(
    projectLiveness('/home/me/webapp-agent-state/.agent-project', child).via, 'engine');
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
