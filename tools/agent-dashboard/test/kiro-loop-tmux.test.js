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

// script の内容で応答を返すスタブ（呼び出し順に依存しない）
function stubShInWsl(handlers) {
  return (script) => {
    for (const [pattern, res] of handlers) {
      if (script.includes(pattern)) {
        return typeof res === 'function' ? res(script) : res;
      }
    }
    return { ok: true, stdout: '', stderr: '', error: '', status: 0 };
  };
}

function okOut(stdout) {
  return { ok: true, stdout, stderr: '', error: '', status: 0 };
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

test('normalizeLinuxPath は Windows ドライブパスを /mnt/<drive> へ寄せる', () => {
  assert.strictEqual(tmux.normalizeLinuxPath('C:\\dev\\app'), '/mnt/c/dev/app');
  assert.strictEqual(exec.toWslCwd('D:/work/repo'), '/mnt/d/work/repo');
  assert.strictEqual(exec.winDriveToWsl('/home/me/repo'), '');   // POSIX は対象外
});

test('listSessions は repo digest または cwd で絞る（セッション名接頭辞由来）', () => {
  const repo = '/home/me/app';
  const digest = tmux.pathDigest(repo);
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut('')],                                    // 状態ファイル無し
    ['list-sessions', okOut(`kiro-loop-app-${digest}-abcd\nkiro-loop-other-ffffffff-zzzz\n`)],
    [`list-panes -t 'kiro-loop-app-${digest}-abcd'`, okOut('%1\t/home/me/app\tkiro\t1\n')],
    ["list-panes -t 'kiro-loop-other-ffffffff-zzzz'", okOut('%2\t/home/me/other\tkiro\t1\n')],
  ]);
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

test('listSessions は send の既定セッション（kiro）も既定接頭辞で拾う', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut('')],
    ['list-sessions', okOut('kiro\nmain\n')],
    ["list-panes -t 'kiro'", okOut('%3\t/home/me/app\tkiro\t1\n')],
  ]);
  try {
    const res = tmux.listSessions({ repo: '/home/me/app' });   // prefix 省略 = 既定 'kiro'
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.items.length, 1);
    assert.strictEqual(res.items[0].session, 'kiro');
    assert.strictEqual(res.items[0].target, '%3');
  } finally {
    exec.shInWsl = orig;
  }
});

test('listSessions は tmux セッション内で起動されたデーモンのペインを状態ファイルから見つける', () => {
  // kiro-loop を人の tmux セッション（名前 main）の中で起動すると、ワーカーペインは
  // main セッション内に作られ `tmux ls` の名前（kiro-loop-…）では発見できない。
  // ~/.kiro/loop-state/<pid>.json の pane_id 直参照で見つけることを検証する。
  const state = JSON.stringify({
    pid: 1234,
    cwd: '/home/me/app',
    sessions: [
      { name: '毎朝レビュー', id: 'review', pane: '%12', alive: true },
      { name: '死んだペイン', id: 'dead', pane: '%99', alive: false },
    ],
  });
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut(`\u001e${state}`)],
    ['list-panes -a', okOut('%12\tmain\t/home/me/app\tkiro\t1\n%1\tmain\t/home/me\tzsh\t0\n')],
    ['list-sessions', okOut('main\n')],   // kiro 接頭辞のセッションは無い
  ]);
  try {
    const res = tmux.listSessions({ repo: '/home/me/app' });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.items.length, 1, '生きているワーカーペインだけを出す');
    assert.strictEqual(res.items[0].target, '%12', 'pane_id を直接視聴ターゲットにする');
    assert.strictEqual(res.items[0].session, 'main', '実際に属するセッション名を表示に使う');
    assert.strictEqual(res.items[0].name, '毎朝レビュー', 'プロンプト名で識別できる');
  } finally {
    exec.shInWsl = orig;
  }
});

test('listSessions は Windows ドライブ上の repo と /mnt の cwd を突き合わせる', () => {
  const state = JSON.stringify({
    pid: 5,
    cwd: '/mnt/c/dev/app',
    sessions: [{ name: 'ジョブ', id: 'j', pane: '%7', alive: true }],
  });
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut(`\u001e${state}`)],
    ['list-panes -a', okOut('%7\twork\t/mnt/c/dev/app\tkiro\t1\n')],
    ['list-sessions', okOut('work\n')],
  ]);
  try {
    const res = tmux.listSessions({ repo: 'C:\\dev\\app' });
    assert.strictEqual(res.items.length, 1);
    assert.strictEqual(res.items[0].target, '%7');
  } finally {
    exec.shInWsl = orig;
  }
});

test('readLoopStates は壊れた状態ファイルをスキップする', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut('\u001e{ broken json\u001e{"pid": 1, "cwd": "/home/me", "sessions": []}')],
  ]);
  try {
    const states = tmux.readLoopStates();
    assert.strictEqual(states.length, 1);
    assert.strictEqual(states[0].pid, 1);
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
