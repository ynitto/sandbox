'use strict';

// 前面に無いときの通知（src/main/notify.js）。Electron は起動せず、窓と Notification を差し替える。

const { test } = require('node:test');
const assert = require('node:assert');
const notify = require('../src/main/notify');

function fakeElectron(shown) {
  return {
    Notification: class {
      constructor(options) { this.options = options; this.handlers = {}; shown.push(this); }
      on(event, fn) { this.handlers[event] = fn; }
      show() { this.visible = true; }
      static isSupported() { return true; }
    },
  };
}

function fakeWindow(extra = {}) {
  return {
    focused: false, flashed: false, focusCount: 0,
    isDestroyed: () => false,
    isFocused() { return this.focused; },
    focus() { this.focusCount += 1; },
    flashFrame(on) { this.flashed = on; },
    ...extra,
  };
}

test('通知の 1 行は「名前 · 状態」で、内部の綴りを出さない', () => {
  assert.strictEqual(notify.notificationText({ kind: 'attention', name: 'ログイン画面の工程を直す' }), 'ログイン画面の工程を直す · 確認待ち');
  assert.strictEqual(notify.notificationText({ kind: 'done', name: '月次集計' }), '月次集計 · 応答が終わりました');
  assert.strictEqual(notify.notificationText({ kind: 'taskFailed', name: '月次集計' }), '月次集計 · 実行に失敗しました');
  // 名前が無い・知らない種類は知らせない
  assert.strictEqual(notify.notificationText({ kind: 'attention', name: '  ' }), '');
  assert.strictEqual(notify.notificationText({ kind: 'busy', name: '会話' }), '');
});

test('ターンの結果とタスクの結果を、通知の種類へ写す', () => {
  assert.strictEqual(notify.turnKind({}), 'done');
  assert.strictEqual(notify.turnKind({ error: '認証切れ' }), 'failed');
  // 利用者が止めたターンは知らせない（止めた人は画面を見ている）
  assert.strictEqual(notify.turnKind({ stopped: true }), '');
  assert.strictEqual(notify.taskRunKind({ ok: true }), 'taskDone');
  assert.strictEqual(notify.taskRunKind({ ok: false, escalate: true }), 'taskAttention');
  assert.strictEqual(notify.taskRunKind({ ok: false }), 'taskFailed');
});

test('前面にあるときは通知しない。前面に無いときだけ 1 行出す', () => {
  const shown = [];
  const win = fakeWindow();
  const notifier = notify.createNotifier({ getWindow: () => win, electron: fakeElectron(shown) });
  win.focused = true;
  assert.strictEqual(notifier.show({ kind: 'attention', name: '会話' }), null);
  assert.strictEqual(shown.length, 0);
  win.focused = false;
  assert.ok(notifier.show({ kind: 'attention', name: '会話' }));
  assert.deepStrictEqual(shown.map((item) => item.options.title), ['会話 · 確認待ち']);
});

test('設定で切っていれば出さない。押したらその会話を開く', () => {
  const shown = [];
  const win = fakeWindow();
  let on = false;
  const opened = [];
  const notifier = notify.createNotifier({
    getWindow: () => win, enabled: () => on, open: (event) => opened.push(event.id), electron: fakeElectron(shown),
  });
  assert.strictEqual(notifier.show({ kind: 'done', name: '会話', id: 's1' }), null);
  on = true;
  const notification = notifier.show({ kind: 'done', name: '会話', id: 's1' });
  assert.ok(notification);
  notification.handlers.click();
  assert.deepStrictEqual(opened, ['s1']);
  assert.strictEqual(win.focusCount, 1);
});

test('窓が無い・壊れているときは何もしない', () => {
  const shown = [];
  const gone = fakeWindow({ isDestroyed: () => true });
  assert.strictEqual(notify.createNotifier({ getWindow: () => null, electron: fakeElectron(shown) }).show({ kind: 'done', name: '会話' }), null);
  assert.strictEqual(notify.createNotifier({ getWindow: () => gone, electron: fakeElectron(shown) }).show({ kind: 'done', name: '会話' }), null);
  assert.strictEqual(shown.length, 0);
});
