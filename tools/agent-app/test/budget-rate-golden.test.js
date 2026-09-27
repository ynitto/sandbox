'use strict';

// node-budget のトークン推定のゴールデン（agent-app 側）。
//
// agent-app は rate / rowTokens も較正器も持たない。持つのは台帳の書き手（audit.row）と、
// 監査の連鎖で agent-audit calibrate --write を呼ぶ段（STEPS）だけ。なのでここでは
// 「agent-app が書いた行を Python の row_tokens が読むと、共有フィクスチャの期待値になるか」を
// 書き手の側から固定する。期待値そのものは agentcore/tests/test_nodebudget_golden.py が確かめる。
// フィクスチャは schemas/node-budget-rates.golden.json の 1 つだけ（写すと片方だけ直る）。
// **挙動は変えない**——明示の null を 0 に変えて書く今の振る舞いも、そのまま記録する。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const audit = require('../src/main/audit');

const golden = JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', '..', 'schemas', 'node-budget-rates.golden.json'), 'utf8'));

// row_tokens が読む列だけを抜く。
function read(row) {
  return { agent_cli: row.agent_cli, model: row.model, seconds: row.seconds, tokens_in: row.tokens_in, tokens_out: row.tokens_out };
}

test('agent-app writes the columns row_tokens reads as the fixture has them', () => {
  for (const c of golden.cases) {
    const written = read(audit.row({ ...c.row, status: 'done' }, { now: 0, node: 'n' }));
    const override = c.agent_app_writes;
    const want = {
      agent_cli: c.row.agent_cli,
      model: c.row.model,
      seconds: c.row.seconds,
      // 列が無い行は null で書く。row_tokens は None を「未報告」と読むので値は変わらない。
      tokens_in: override ? override.tokens_in : (c.row.tokens_in === undefined ? null : c.row.tokens_in),
      tokens_out: override ? override.tokens_out : (c.row.tokens_out === undefined ? null : c.row.tokens_out),
    };
    assert.deepEqual(written, want, c.name);
  }
});

test('an explicit null token count is written as 0, so the row reads as measured zero', () => {
  const c = golden.cases.find((item) => item.agent_app_writes);
  assert.ok(c, 'フィクスチャに agent_app_writes の行が要る');
  assert.equal(c.row.tokens_in, null);
  assert.ok(c.expected.rated.row_tokens > 0, '書く前の行は 秒 × レートで数えられる');
  assert.equal(c.agent_app_writes.expected.rated.row_tokens, 0, '書いた行は実測 0 と読まれる');
});

test('agent-app has no calibrator of its own and runs agent-audit calibrate --write', () => {
  const step = audit.STEPS.find((s) => s.key === 'calibrate');
  assert.deepEqual(step.args, ['calibrate', '--write']);
});
