'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const InputMode = require('../src/renderer/inputMode');

test('入力先は明示的にメッセージと端末操作を切り替える', () => {
  let state = InputMode.create();
  assert.strictEqual(state.mode, 'message');

  state = InputMode.reduce(state, { type: 'terminal-focus' });
  assert.strictEqual(state.mode, 'terminal');

  state = InputMode.reduce(state, { type: 'message-focus' });
  assert.strictEqual(state.mode, 'message');
});

test('端末操作のEscapeは1回目をCLIへ送り2回目でメッセージへ戻る', () => {
  let state = InputMode.reduce(InputMode.create(), { type: 'terminal-focus' });

  let result = InputMode.handleEscape(state, 1000);
  assert.strictEqual(result.forward, true);
  assert.strictEqual(result.state.mode, 'terminal');

  result = InputMode.handleEscape(result.state, 1400);
  assert.strictEqual(result.forward, false);
  assert.strictEqual(result.state.mode, 'message');
});

test('入力モードは未知イベントとメッセージ中のEscapeを無視する', () => {
  const state = InputMode.create();
  assert.strictEqual(InputMode.reduce(state, { type: 'unknown' }), state);
  assert.deepStrictEqual(InputMode.handleEscape(state, 1000), { state, forward: false });
});

test('端末操作のEscape間隔が空いた場合は両方をCLIへ送る', () => {
  let result = InputMode.handleEscape(InputMode.reduce(InputMode.create(), { type: 'terminal-focus' }), 1000);
  result = InputMode.handleEscape(result.state, 1700);
  assert.strictEqual(result.forward, true);
  assert.strictEqual(result.state.mode, 'terminal');
});

test('ブラウザでは入力モードAPIをwindowへ公開する', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'inputMode.js'), 'utf8');
  const context = { window: {} };
  vm.runInNewContext(source, context);
  assert.strictEqual(context.window.InputMode.create().mode, 'message');
});
