'use strict';

// host.yaml（ノード固有の repos[] 宣言・S3）の**置き場の解決**と、宣言された `local` を
// この画面から開ける形へ寄せる変換の回帰テスト。
//
// 正典構成は「Windows の画面 + WSL の実行エンジン」。この 2 つを os.homedir() 基準で
// 解決すると、宣言はあるのに一度も読まれず、CLIチャットの起動先も検収差分も
// 「この PC にローカルクローンの宣言がありません（repos[] に書くと選べます）」と案内し続ける
// ——書いてある人には直しようがない。engine/status.json で踏んだ P0-2 と同じ壊れ方なので、
// ここで構造として固定する。

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const nodeRepos = require('../src/features/agent-project/main/nodeRepos');

function mkAgentsHome(hostYaml) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'node-repos-'));
  const home = path.join(root, '.agents');
  fs.mkdirSync(home, { recursive: true });
  if (hostYaml !== null) {
    fs.writeFileSync(path.join(home, 'agent-project.host.yaml'), hostYaml, 'utf8');
  }
  return { root, home };
}

function withPlatform(name, fn) {
  const orig = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: name, configurable: true });
  try {
    return fn();
  } finally {
    if (orig) Object.defineProperty(process, 'platform', orig);
  }
}

test('host.yaml は実行エンジンのホームから読む（この PC のホームで解決しない）', () => {
  const { root, home } = mkAgentsHome(
    'repos:\n  - url: https://gitea.example/you/app.git\n    local: /home/dev/mirrors/app\n'
  );
  try {
    // engine.home を明示した設定は、そのまま置き場になる（engine.agentsHome の契約）。
    const got = nodeRepos.loadNodeRepos({ engine: { home } });
    assert.deepStrictEqual(got.map((e) => e.url), ['https://gitea.example/you/app.git']);
    assert.strictEqual(got[0].local, '/home/dev/mirrors/app');

    // 設定を渡さない呼び出しがこのホームを拾ってしまうと、テストが偶然通るだけになる。
    assert.strictEqual(nodeRepos.agentsHome({ engine: { home } }), home);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('宣言が無ければ空配列（呼び出し側は理由付きの非活性へ倒す）', () => {
  const { root, home } = mkAgentsHome(null);
  try {
    assert.deepStrictEqual(nodeRepos.loadNodeRepos({ engine: { home } }), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('AGENT_PROJECT_AGENTS_HOME は設定より優先される（agentcore.repolocal と共通）', () => {
  const { root, home } = mkAgentsHome('repos:\n  - url: https://h/t/a.git\n    local: /m/a\n');
  const prev = process.env.AGENT_PROJECT_AGENTS_HOME;
  process.env.AGENT_PROJECT_AGENTS_HOME = home;
  try {
    assert.strictEqual(nodeRepos.agentsHome({ engine: { home: '/nowhere' } }), home);
    assert.deepStrictEqual(
      nodeRepos.loadNodeRepos({ engine: { home: '/nowhere' } }).map((e) => e.url),
      ['https://h/t/a.git']
    );
  } finally {
    if (prev === undefined) delete process.env.AGENT_PROJECT_AGENTS_HOME;
    else process.env.AGENT_PROJECT_AGENTS_HOME = prev;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('local の POSIX パスは win32 で UNC へ寄せる（設定のディストロを使う）', () => {
  // 変換せずに statSync へ渡すと現在のドライブ基準に解決されて必ず外れ、宣言はあるのに
  // 「実体が無い」＝選べない、になる。engine/status.json の children[].root と同じ規則。
  withPlatform('win32', () => {
    assert.strictEqual(
      nodeRepos.viewerLocalPath('/home/dev/mirrors/app', { engine: { distro: 'Ubuntu' } }),
      '\\\\wsl.localhost\\Ubuntu\\home\\dev\\mirrors\\app'
    );
  });
});

test('local の変換は設定のディストロを既定へ丸めない（別ディストロの空振りを防ぐ）', () => {
  withPlatform('win32', () => {
    assert.strictEqual(
      nodeRepos.viewerLocalPath('/home/dev/app', { engine: { distro: 'Debian' } }),
      '\\\\wsl.localhost\\Debian\\home\\dev\\app'
    );
  });
});

test('Windows のドライブパスと POSIX 以外はそのまま（変換しない）', () => {
  withPlatform('win32', () => {
    assert.strictEqual(
      nodeRepos.viewerLocalPath('C:\\src\\app', { engine: { distro: 'Ubuntu' } }),
      'C:\\src\\app'
    );
  });
  // 画面自体が Linux で動く場合は寄せる先が無い（POSIX パスがそのまま実体）。
  withPlatform('linux', () => {
    assert.strictEqual(
      nodeRepos.viewerLocalPath('/home/dev/app', { engine: { distro: 'Ubuntu' } }),
      '/home/dev/app'
    );
  });
});

test('resolveLocalRepo は変換後のパスで実在を確かめる（POSIX のまま素通りさせない）', () => {
  // 画面が win32 なら、実行側が宣言した POSIX パスの実体は UNC 側にしかない。ここで変換を
  // 飛ばして statSync が通ってしまうと、Windows から開けないパスを「選べる」と表示し、
  // CLIチャットが起動時に落ちる。
  const real = fs.mkdtempSync(path.join(os.tmpdir(), 'clone-posix-'));
  try {
    const declared = [{ url: 'https://h/t/app.git', local: real }];
    withPlatform('win32', () => {
      assert.strictEqual(
        nodeRepos.resolveLocalRepo('https://h/t/app.git', declared, { engine: { distro: 'Ubuntu' } }),
        '',
        'UNC へ寄せた先が無いなら「実体なし」（POSIX パスをそのまま返さない）'
      );
    });
  } finally {
    fs.rmSync(real, { recursive: true, force: true });
  }
});

test('resolveLocalRepo は実在するクローンだけを返す（宣言だけでは選ばせない）', () => {
  const real = fs.mkdtempSync(path.join(os.tmpdir(), 'clone-'));
  try {
    const declared = [
      { url: 'https://h/t/app.git', local: real },
      { url: 'https://h/t/gone.git', local: path.join(real, 'does-not-exist') },
    ];
    assert.strictEqual(nodeRepos.resolveLocalRepo('https://h/t/app.git', declared, {}), real);
    assert.strictEqual(nodeRepos.resolveLocalRepo('https://h/t/gone.git', declared, {}), '');
    assert.strictEqual(nodeRepos.resolveLocalRepo('https://h/t/other.git', declared, {}), '');
  } finally {
    fs.rmSync(real, { recursive: true, force: true });
  }
});
