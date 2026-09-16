'use strict';

// 「直前のセッション」を再開する CLI の混線の注意（agentCli.continueClashWarning）と、ipc の配線。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const agentCli = require('../src/main/agentCli');

const continueSpec = { name: 'ponytail', session: null, continueArgs: ['--continue'], interactive: null };
const interactiveContinueSpec = { name: 'tui', session: null, continueArgs: [], interactive: { continueArgs: ['-c'] } };
const mintSpec = { name: 'claude', session: agentCli.SESSION.claude, continueArgs: ['--continue'], interactive: null };
const replaySpec = { name: 'plain', session: null, continueArgs: [], interactive: { continueArgs: [] } };

test('continue_args だけを持つ CLI（ヘッドレスか対話起動のどちらか）を「直前のセッションを拾う」と見る', () => {
  assert.strictEqual(agentCli.continuesLatest(continueSpec), true);
  assert.strictEqual(agentCli.continuesLatest(interactiveContinueSpec), true);
  assert.strictEqual(agentCli.continuesLatest(mintSpec), false);   // ID を発行するので混線しない
  assert.strictEqual(agentCli.continuesLatest(replaySpec), false);  // 履歴を再送するだけ
  assert.strictEqual(agentCli.continuesLatest(null), false);
});

test('同じリポジトリ・同じ CLI で応答中の別の会話があるときだけ 1 行返す', () => {
  const turn = { id: 'a', repo: '/repo', cli: 'ponytail', spec: continueSpec };
  const active = [
    { id: 'a', repo: '/repo', cli: 'ponytail', name: '自分' },          // 自分は数えない
    { id: 'b', repo: '/repo', cli: 'ponytail', name: 'テスト直し' },
    { id: 'c', repo: '/other', cli: 'ponytail', name: '別リポジトリ' },
    { id: 'd', repo: '/repo', cli: 'codex', name: '別 CLI' },
  ];
  const warning = agentCli.continueClashWarning(turn, active);
  assert.match(warning, /ponytail は直前のセッションを再開する CLI です/);
  assert.match(warning, /「テスト直し」が応答中/);
  assert.ok(!warning.includes('別リポジトリ') && !warning.includes('別 CLI') && !warning.includes('自分'));
  assert.strictEqual(agentCli.continueClashWarning(turn, active.filter((t) => t.id !== 'b')), '');
  assert.strictEqual(agentCli.continueClashWarning({ ...turn, spec: mintSpec }, active), '');
  assert.strictEqual(agentCli.continueClashWarning(turn, []), '');
  assert.strictEqual(agentCli.continueClashWarning(turn, undefined), '');
});

test('ipc は送る前に注意を組み、警告（turn:started）と実行情報の両方に載せる', () => {
  const ipc = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'ipc.js'), 'utf8');
  assert.match(ipc, /agentCli\.continueClashWarning\(\{ id, repo: sess\.repo, cli: base\.cli, spec \}, activeTurns\(ud\)\)/);
  assert.match(ipc, /setupWarning = \[setupWarning, clash\]\.filter\(Boolean\)\.join\('\\n'\)/);
  assert.match(ipc, /setupInformation\.push\(\{ type: 'status', title: clash, status: 'attention' \}\)/);
  // ヘッドレスの子プロセスも CLI 名を持ち、応答中の一覧（activeTurns）に CLI が出る
  assert.match(ipc, /child\.cli = cli;\n\s*running\.set\(id, child\)/);
  assert.match(ipc, /for \(const conv of conversations\.values\(\)\) if \(conv\.turn\) push\(conv\.id, conv\.launch && conv\.launch\.cli\)/);
});
