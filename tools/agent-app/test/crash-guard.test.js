'use strict';

// 落ち方の判断（src/main/crashGuard.js）を Electron 無しで固定する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crashGuard = require('../src/main/crashGuard');

function fakeDialog() {
  const calls = { errorBoxes: [], messages: [] };
  return {
    calls,
    showErrorBox: (title, detail) => { calls.errorBoxes.push({ title, detail }); },
    showMessageBox: async (options) => { calls.messages.push(options); return { response: calls.answer == null ? 0 : calls.answer }; },
  };
}

test('例外はログに残し、ダイアログは 1 回だけ出す。未処理の Promise はログだけ', () => {
  const lines = [];
  const dialog = fakeDialog();
  const guard = crashGuard.createCrashGuard({ log: (kind, detail) => lines.push([kind, detail]), dialog });
  guard.onUncaughtException(new Error('boom'));
  guard.onUncaughtException(new Error('again'));
  guard.onUnhandledRejection(new Error('later'));
  assert.deepStrictEqual(lines.map(([kind]) => kind), ['uncaughtException', 'uncaughtException', 'unhandledRejection']);
  assert.strictEqual(dialog.calls.errorBoxes.length, 1);
  assert.match(dialog.calls.errorBoxes[0].detail, /boom/);
  assert.match(dialog.calls.errorBoxes[0].detail, /crash\.log/);
});

test('画面が落ちたら読み直す。1 分に 4 回目からは読み直さず知らせる。自分で閉じた分は無視', () => {
  let t = 0;
  const dialog = fakeDialog();
  const guard = crashGuard.createCrashGuard({ log: () => {}, dialog, now: () => t });
  assert.strictEqual(guard.onRenderProcessGone({ reason: 'clean-exit', exitCode: 0 }), 'ignore');
  assert.strictEqual(guard.onRenderProcessGone({ reason: 'killed', exitCode: 0 }), 'ignore');
  for (let i = 0; i < crashGuard.MAX_RELOADS; i += 1) {
    t += 1000;
    assert.strictEqual(guard.onRenderProcessGone({ reason: 'crashed', exitCode: 5 }), 'reload');
  }
  t += 1000;
  assert.strictEqual(guard.onRenderProcessGone({ reason: 'crashed', exitCode: 5 }), 'stop');
  assert.strictEqual(dialog.calls.errorBoxes.length, 1);
  // 窓（1 分）を過ぎればまた読み直す
  t += crashGuard.RELOAD_WINDOW_MS + 1;
  assert.strictEqual(guard.onRenderProcessGone({ reason: 'oom', exitCode: 1 }), 'reload');
});

test('固まったときは「待つ」が既定で、「読み直す」を選んだときだけ reload', async () => {
  const dialog = fakeDialog();
  const guard = crashGuard.createCrashGuard({ log: () => {}, dialog });
  assert.strictEqual(await guard.onUnresponsive(), 'wait');
  dialog.calls.answer = 1;
  assert.strictEqual(await guard.onUnresponsive(), 'reload');
  assert.deepStrictEqual(dialog.calls.messages[0].buttons, ['待つ', '読み直す']);
  assert.strictEqual(dialog.calls.messages[0].defaultId, 0);
});

test('ログは userData/logs/crash.log に 1 行ずつ足し、上限を超えたら .1 へ回す', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'crash-log-'));
  const file = path.join(dir, 'logs', 'crash.log');
  const log = crashGuard.createLogger(file);
  log('uncaughtException', new Error('first'));
  log('unhandledRejection', { code: 'X' });
  const text = fs.readFileSync(file, 'utf8');
  assert.match(text, /\[uncaughtException\] Error: first/);
  assert.match(text, /\[unhandledRejection\] {"code":"X"}/);
  fs.writeFileSync(file, 'x'.repeat(1024 * 1024));
  log('unresponsive', 'again');
  assert.ok(fs.existsSync(`${file}.1`));
  assert.match(fs.readFileSync(file, 'utf8'), /^\S+ \[unresponsive\] again\n$/);
});

test('main.js は起動時に落ち方の見張りを付け、窓にも付ける', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'main.js'), 'utf8');
  assert.match(main, /crashGuard\.install\(\{ app, dialog, userData: app\.getPath\('userData'\) \}\)/);
  assert.match(main, /guard\.attach\(win\)/);
});
