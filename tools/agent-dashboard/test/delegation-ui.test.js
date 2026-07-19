'use strict';

// 委譲は agent-flow / agent-amigos 間の内部契約として維持するが、利用者向けの
// 独立タブにはしない。依頼・参加・受け入れはそれぞれミッション／要対応／実行の
// 目的別画面から行い、workload・busDir・roles JSON などの内部概念を露出しない。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const rendererDir = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(rendererDir, 'index.html'), 'utf8');

test('利用者向けナビゲーションに委譲タブを表示しない', () => {
  assert.ok(!html.includes('data-tab="delegation"'), '委譲タブボタンを置かない');
  assert.ok(!html.includes('id="tab-delegation"'), '委譲専用ペインを置かない');
});

test('内部契約のデバッグ画面を renderer に読み込まない', () => {
  assert.ok(!html.includes('features/delegation.js'), '委譲専用 renderer を読み込まない');
});

test('利用者の操作先は目的別の画面として残す', () => {
  assert.ok(html.includes('data-tab="needs"'), '判断と承認は要対応に集約する');
  assert.ok(html.includes('data-tab="flow"'), '進行状況は実行に集約する');
  assert.ok(html.includes('data-tab="amigos"'), '依頼・参加・受け入れはミッションに集約する');
});

console.log(`\n${passed} tests passed`);
