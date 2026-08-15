'use strict';

const assert = require('assert');
const compiler = require('../src/features/orchestration/main/execution-policy-compiler');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function qualification(id, status, lower, cost, p50, evaluated = '2026-08-14T00:00:00Z') {
  return {
    qualification_id: id,
    status,
    samples: 9,
    passed: Math.round(lower * 9),
    success_rate_lower_bound: lower,
    critical_failure_risk: status === 'blocked' ? 1 : 0,
    p50_seconds: p50,
    expected_calls_with_failures: lower > 0 ? 1 / lower : null,
    last_evaluated_at: evaluated,
    valid_until: '2026-11-12T00:00:00Z',
    source: 'eval-archive',
    economics: { estimated_cost: cost },
  };
}

function fixture() {
  return {
    version: 1,
    revision: 12,
    candidates: [
      { agent_cli: 'aider', model: 'gemma4:e4b', economics: { estimated_cost: 0 },
        qualifications: { repair: qualification('aider-repair-v1', 'qualified', 0.72, 0, 141) } },
      { agent_cli: 'cursor', model: 'grok-4.5', economics: { estimated_cost: 1 },
        qualifications: { general: qualification('cursor-general-v1', 'qualified', 0.9, 1, 30) } },
      { agent_cli: 'aider', model: 'gemma4:12b', economics: { estimated_cost: 0 },
        qualifications: { code: qualification('aider-12b-code-v1', 'blocked', 0, 0, 600) } },
      { agent_cli: 'claude', model: 'opus', economics: { estimated_cost: 3 },
        qualifications: { general: qualification('claude-opus-v1', 'qualified', 0.95, 3, 20) } },
    ],
  };
}

const tiers = {
  basic: { order: 0, candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }] },
  small: { order: 1, candidates: [{ agent_cli: 'aider', model: 'gemma4:12b' }] },
  medium: { order: 2, candidates: [{ agent_cli: 'cursor', model: 'grok-4.5' }] },
  large: { order: 3, candidates: [{ agent_cli: 'claude', model: 'opus' }] },
};

test('balanced は適格性を優先して順位を固定し、blocked と上限超過を除外する', () => {
  const input = { strategy: 'balanced', tiers, tierCeiling: 'medium', qualifications: fixture() };
  const first = compiler.compileSelectionPolicy(input);
  const second = compiler.compileSelectionPolicy(input);
  assert.deepStrictEqual(first, second, '同じ入力とrevisionなら同じ出力');
  assert.strictEqual(first.qualification_revision, 12);
  assert.deepStrictEqual(first.candidates.map((candidate) => [
    candidate.agent_cli, candidate.model, candidate.rank,
  ]), [
    ['cursor', 'grok-4.5', 1],
    ['aider', 'gemma4:e4b', 2],
  ]);
  assert.deepStrictEqual(first.candidates[0].qualification_refs, ['cursor-general-v1']);
  assert.ok(!first.candidates.some((candidate) => candidate.model === 'gemma4:12b'));
  assert.ok(!first.candidates.some((candidate) => candidate.model === 'opus'));
});

test('economy は実行場所でなく予想費用を先に比較する', () => {
  const policy = compiler.compileSelectionPolicy({
    strategy: 'economy', tiers, tierCeiling: 'medium', qualifications: fixture(),
  });
  assert.deepStrictEqual(policy.candidates.map((candidate) => candidate.model), [
    'gemma4:e4b', 'grok-4.5',
  ]);
});

test('quality は成功率、評価の新しさ、サンプル数の辞書順で比較する', () => {
  const doc = {
    version: 1, revision: 3,
    candidates: [
      { agent_cli: 'older', model: 'm', economics: { estimated_cost: 1 }, qualifications: {
        review: { ...qualification('older-review', 'qualified', 0.9, 1, 20, '2026-08-01T00:00:00Z'), samples: 30 },
      } },
      { agent_cli: 'newer', model: 'm', economics: { estimated_cost: 2 }, qualifications: {
        review: { ...qualification('newer-review', 'qualified', 0.9, 2, 30, '2026-08-14T00:00:00Z'), samples: 10 },
      } },
      { agent_cli: 'best', model: 'm', economics: { estimated_cost: 3 }, qualifications: {
        review: qualification('best-review', 'qualified', 0.95, 3, 40, '2026-07-01T00:00:00Z'),
      } },
    ],
  };
  const policy = compiler.compileSelectionPolicy({
    strategy: 'quality',
    tiers: { medium: { order: 2, candidates: doc.candidates.map((c) =>
      ({ agent_cli: c.agent_cli, model: c.model })) } },
    tierCeiling: 'medium', qualifications: doc,
  });
  assert.deepStrictEqual(policy.candidates.map((candidate) => candidate.agent_cli), [
    'best', 'newer', 'older',
  ]);
});

test('期限切れ・unknown・壊れた候補を除外し、trialと複数の適格根拠を保持する', () => {
  const valid = qualification('trial-one', 'trial', 0.5, 0, 10);
  const second = qualification('trial-two', 'qualified', 0.6, 0, 9);
  const expired = { ...qualification('expired', 'qualified', 0.9, 0, 1),
    valid_until: '2026-01-01T00:00:00Z' };
  const doc = { version: 1, revision: 4, candidates: [
    null,
    { agent_cli: 'trial', model: 'm', qualifications: {
      first: valid, second, noId: { status: 'qualified' }, bad: null,
    } },
    { agent_cli: 'expired', model: 'm', qualifications: { work: expired } },
    { agent_cli: 'unknown', model: 'm', qualifications: {
      work: { qualification_id: 'unknown', status: 'unknown' },
    } },
    { agent_cli: 'invalid-date', model: 'm', qualifications: {
      work: { ...valid, qualification_id: 'invalid-date', valid_until: 'not-a-date' },
    } },
    { agent_cli: 'empty', model: 'm' },
  ] };
  const tiersWithNoise = {
    broken: { order: 'not-a-number', candidates: [] },
    medium: { order: 2, candidates: [
      { agent_cli: 'trial', model: 'm' },
      { agent_cli: 'trial', model: 'm' },
      { agent_cli: 'expired', model: 'm' },
      { agent_cli: 'unknown', model: 'm' },
      { agent_cli: 'invalid-date', model: 'm' },
      { agent_cli: 'empty', model: 'm' },
      { agent_cli: '', model: 'm' }, null,
    ] },
    large: { order: 3, candidates: [{ agent_cli: 'outside', model: 'm' }] },
  };
  const policy = compiler.compileSelectionPolicy({
    tiers: tiersWithNoise, tierCeiling: 'medium', qualifications: doc,
    nowMs: Date.parse('2026-08-15T00:00:00Z'),
  });
  assert.strictEqual(policy.candidates.length, 1);
  assert.deepStrictEqual(policy.candidates[0].qualification_refs, ['trial-one', 'trial-two']);
});

test('不正strategyと実行レベル未定義は安全側へ倒す', () => {
  assert.throws(() => compiler.compileSelectionPolicy({ strategy: 'local-first' }), /strategy が不正/);
  assert.deepStrictEqual(compiler.compileSelectionPolicy({
    tiers, tierCeiling: 'not-found', qualifications: fixture(),
  }).candidates, []);
  assert.deepStrictEqual(compiler.compileSelectionPolicy({
    tiers: { medium: { order: 2 } }, tierCeiling: 'medium', qualifications: null,
  }).candidates, []);
});

console.log(`\n${passed} tests passed (execution-policy-compiler)`);
