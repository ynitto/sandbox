'use strict';

// flow:cancel の「終端 run では revise（タスク積み直し）しない」契約をソースで固定する。
// Electron ipcMain を起こさず、既に終端した archival cancel で settled タスクが
// ready に戻らないことを担保する。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ipcSource = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'main', 'ipc.js'),
  'utf8'
);

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('flow:cancel は alreadyTerminal のとき revise しない', () => {
  const block = ipcSource.match(/handle\('flow:cancel'[\s\S]*?\n {2}\}\);/);
  assert.ok(block, 'flow:cancel ハンドラがある');
  assert.ok(
    /alreadyTerminal/.test(block[0]),
    '終端判定を見てから detach する'
  );
  assert.ok(
    /!\(res && res\.alreadyTerminal\)/.test(block[0])
      || /!res\.alreadyTerminal/.test(block[0]),
    'alreadyTerminal なら runAction(revise) へ進まない'
  );
});

test('flow:cancel は非終端のときだけ revise で detach する', () => {
  const block = ipcSource.match(/handle\('flow:cancel'[\s\S]*?\n {2}\}\);/);
  assert.ok(block);
  assert.ok(/action:\s*'revise'/.test(block[0]), '非終端は revise で project 契約に乗せる');
});

console.log(`\n${passed} tests passed (flow-cancel-detach)`);
