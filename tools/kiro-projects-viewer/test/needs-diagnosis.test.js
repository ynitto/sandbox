'use strict';

// 要対応カードの「失敗の要因」要約と、差分の内訳（成果物 / 内部の実行記録）を検証する。
// 追加依存なしで `node test/needs-diagnosis.test.js` で走る。
//
// 背景: blocked カードは verify の生出力をそのまま「なぜ:」に貼り、判断材料には差分ファイルを
// 列挙する。ところが kiro-flow は実行のたびに bus/runs/... へ大量の内部記録を書くため、
// エージェントが成果物を 1 つも出せずに終わった run でも「14 ファイル変更」と見え、しかも
// 中身は claims/events/results ばかりで、人には何が起きたのか読み取れなかった。

const assert = require('assert');
const kiro = require('../src/main/kiro');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function card(why, detail) {
  return `---
kind: blocked
task-id: T-1
---

# 要対応: T-1 — 何かをする

## Context and Problem Statement

- なぜ: ${why}
- 状態: blocked（kiro-project の判断待ち）

## 判断材料（成果物の所在・差分・検証）
${detail}
`;
}

test('検証コマンドが対象を見つけられない失敗を要約する', () => {
  const n = kiro.parseNeeds(
    card('繰り返し NG（retries=3）: exit=4 no tests ran in 0.00s',
      '- 検証: `pytest tools/x/tests` → FAIL（exit=4 no tests ran ERROR: file or directory not found: tools/x/tests）'),
    'T-1'
  );
  assert.match(n.failureSummary, /tools\/x\/tests/);
  assert.match(n.failureSummary, /見つけられませんでした/);
});

test('連鎖の途中で沈黙した工程は「失敗した工程」として名指しされる', () => {
  // run_verify（kiro-project）が set -x トレースで特定した工程を、そのまま人に見せる。
  // 「exit=1 なのにテストは全部通っている」という読めない失敗の答えがこれ。
  const n = kiro.parseNeeds(
    card(
      '繰り返し NG（retries=3）: exit=1 失敗した工程: `grep -rq codd_gate tools/kiro-project/kiro_project/`（それより前の工程は成功） 29 passed',
      '- 検証: `pytest -k codd && grep -rq codd_gate tools/` → FAIL'
    ),
    'T-1'
  );
  assert.match(n.failureSummary, /grep -rq codd_gate/);
  assert.match(n.failureSummary, /それより前の工程は成功/);
});

test('旧形式（工程の記録なし）でも「テストの失敗ではない」ことは言う', () => {
  // exit≠0 なのに N passed だけが見える古い記録。どこが落ちたかは分からないが、
  // テスト成功の出力だけを見せられて混乱させない。
  const n = kiro.parseNeeds(
    card('繰り返し NG（retries=3）: exit=1 29 passed, 623 deselected in 0.20s', ''),
    'T-1'
  );
  assert.match(n.failureSummary, /テストは 29 件成功/);
  assert.match(n.failureSummary, /後段の工程/);
});

test('テストの失敗件数を要約する', () => {
  const n = kiro.parseNeeds(
    card('繰り返し NG（retries=3）: exit=1', '- 検証: `pytest` → FAIL（exit=1 4 failed, 896 passed）'),
    'T-1'
  );
  assert.strictEqual(n.failureSummary, 'テストが 4 件失敗しました。');
});

test('コマンド不在を要約する', () => {
  const n = kiro.parseNeeds(
    card('繰り返し NG: exit=127', '- 検証: `codd-gate verify` → FAIL（codd-gate: command not found）'),
    'T-1'
  );
  assert.match(n.failureSummary, /codd-gate/);
  assert.match(n.failureSummary, /見つかりません/);
});

test('解釈できない失敗は終了コードだけ添える', () => {
  const n = kiro.parseNeeds(card('繰り返し NG: exit=2', '- 検証: `make all` → FAIL（exit=2 何かがおかしい）'), 'T-1');
  assert.strictEqual(n.failureSummary, '検証コマンドが失敗しました（終了コード 2）。');
});

test('手掛かりが無ければ要約しない（生の情報を隠さない）', () => {
  const n = kiro.parseNeeds(card('人の判断が必要', '- 所在: /srv/p'), 'T-1');
  assert.strictEqual(n.failureSummary, '');
});

test('差分を成果物と内部の実行記録に分ける', () => {
  const n = kiro.parseNeeds(
    card('繰り返し NG: exit=1', [
      '- 成果物: git: 未コミットの変更あり',
      '- 差分: 4 ファイル',
      '    - .kiro-project/bus/runs/run-1/results/t1.json',
      '    - .kiro-project/bus/runs/run-1/events/worker-1.jsonl',
      '    - .kiro-project/journal.md',
      '    - tools/kiro-project/kiro-project.py',
      '- 検証: `pytest` → FAIL（exit=1）',
    ].join('\n')),
    'T-1'
  );
  assert.deepStrictEqual(n.diff.artifacts, ['tools/kiro-project/kiro-project.py']);
  assert.strictEqual(n.diff.internal.length, 3);   // bus/×2 と journal.md
  assert.strictEqual(n.evidenceThin, false);        // 成果物が 1 件ある＝痩せていない
});

test('差分が内部の実行記録だけなら「痩せた判断材料」として印を付ける', () => {
  // エージェントが成果物を 1 つも出せずに終わった run。人にとっては「変更なし」と同じ。
  const n = kiro.parseNeeds(
    card('繰り返し NG（retries=3）: exit=1', [
      '- 成果物: git: 未コミットの変更あり',
      '- 差分: 14 ファイル',
      '    - .kiro-project/bus/runs/run-1/claims/gen1/worker-1.json',
      '    - .kiro-project/bus/runs/run-1/results/gen1.json',
      '    - .kiro-project/claims/T-1.lock',
      '    - …他 2 件',
      '- 検証: `pytest` → FAIL（exit=1）',
    ].join('\n')),
    'T-1'
  );
  assert.deepStrictEqual(n.diff.artifacts, []);
  assert.strictEqual(n.diff.internal.length, 3);
  assert.strictEqual(n.diff.truncated, 2);
  assert.strictEqual(n.evidenceThin, true);
});

test('差分リストは次のセクションで終わる（検証行を取り込まない）', () => {
  const n = kiro.parseNeeds(
    card('NG', [
      '- 差分: 1 ファイル',
      '    - src/app.js',
      '- 検証: `npm test` → FAIL（exit=1）',
      '- 所在: /srv/p',
    ].join('\n')),
    'T-1'
  );
  assert.deepStrictEqual(n.diff.artifacts, ['src/app.js']);
  assert.strictEqual(n.diff.internal.length, 0);
});

console.log(`\n${passed} passed`);
