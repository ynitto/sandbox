'use strict';

const assert = require('assert');

const tmux = require('../src/features/kiro-loop/main/tmux');
const send = require('../src/features/kiro-loop/main/send');
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

const loopState = JSON.stringify({
  pid: 100,
  cwd: '/home/me/app',
  updated_at: 1752800000,
  sessions: [
    { name: 'nightly', id: 'p1', pane: '%5', alive: true, last_sent_at: 1752800100, last_send_ok: true },
    { name: 'hourly', id: 'p2', pane: '%6', alive: true, last_sent_at: 1752800200, last_send_ok: false },
    { name: 'stale', id: 'p3', pane: '%9', alive: true },
  ],
});

test('stateSummary は last_sent_at / alive / busy を突き合わせて返す', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut(`${loopState}`)],
    // %9 はペイン消滅（tmux に存在しない）
    ['list-panes -a', okOut('%5\tkiro\t/home/me/app\tkiro\t1\n%6\tkiro\t/home/me/app\tkiro\t0\n')],
    // %6 だけスロット保持中（busy）
    ['slots/pane_', okOut('/home/me/.kiro/slots/pane_6.json\n')],
  ]);
  try {
    const res = tmux.stateSummary({ repo: '/home/me/app' });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.daemons.length, 1);
    const sessions = res.daemons[0].sessions;
    assert.strictEqual(sessions.length, 3);
    const byName = new Map(sessions.map((s) => [s.name, s]));
    assert.deepStrictEqual(byName.get('nightly'), {
      name: 'nightly', pane: '%5', alive: true, busy: false, lastSentAt: 1752800100, lastSendOk: true,
    });
    assert.strictEqual(byName.get('hourly').busy, true);
    assert.strictEqual(byName.get('hourly').lastSendOk, false);
    // 送信記録なし・ペイン消滅は lastSentAt=0 / alive=false
    assert.strictEqual(byName.get('stale').lastSentAt, 0);
    assert.strictEqual(byName.get('stale').alive, false);
  } finally {
    exec.shInWsl = orig;
  }
});

test('stateSummary は repo 不一致のデーモンを返さない', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = stubShInWsl([
    ['loop-state', okOut(`${loopState}`)],
    ['list-panes -a', okOut('%5\tkiro\t/home/me/app\tkiro\t1\n')],
  ]);
  try {
    const res = tmux.stateSummary({ repo: '/home/me/other' });
    assert.strictEqual(res.daemons.length, 0);
  } finally {
    exec.shInWsl = orig;
  }
});

test('sendPrompt は repo を cwd にして kiro-loop send を呼ぶ', () => {
  const orig = exec.shInWsl;
  let script = '';
  exec.shInWsl = (s) => {
    script = s;
    return okOut('[kiro-loop] 完了しました');
  };
  try {
    const res = send.sendPrompt({ repo: '\\\\wsl.localhost\\Ubuntu\\home\\me\\app', target: '%5', prompt: 'nightly' });
    assert.strictEqual(res.ok, true);
    assert.strictEqual(res.sent, true);
    assert.ok(script.includes("cd '/home/me/app'"));
    assert.ok(script.includes("send -s '%5' 'nightly'"));
  } finally {
    exec.shInWsl = orig;
  }
});

test('sendPrompt は busy 拒否を busy=true として返す（送信待機へ変換できる）', () => {
  const orig = exec.shInWsl;
  exec.shInWsl = () => ({
    ok: false, status: 1, stdout: '',
    stderr: '[kiro-loop] ERROR: ペイン %5 は現在処理中です。完了後に再送してください。',
    error: '',
  });
  try {
    const res = send.sendPrompt({ repo: '/home/me/app', target: '%5', prompt: 'x' });
    assert.strictEqual(res.ok, false);
    assert.strictEqual(res.busy, true);
  } finally {
    exec.shInWsl = orig;
  }
});

test('sendPrompt は同時実行上限も busy として扱う', () => {
  assert.strictEqual(send.isBusyMessage('ERROR: 同時実行数が上限に達しています。'), true);
  assert.strictEqual(send.isBusyMessage('ERROR: kiro-cli が見つかりません'), false);
});

test('sendPrompt は空プロンプトを拒否する', () => {
  const res = send.sendPrompt({ repo: '/home/me/app', target: '%5', prompt: '  ' });
  assert.strictEqual(res.ok, false);
  assert.strictEqual(res.busy, false);
});

console.log(`\n${passed} tests passed`);
