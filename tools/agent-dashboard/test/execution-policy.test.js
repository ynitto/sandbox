'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const executionPolicy = require('../src/features/orchestration/main/execution-policy');
const profiles = require('../src/features/orchestration/main/profiles');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('おまかせは全 workload を medium から残量20%で small へ切り替える', () => {
  const built = executionPolicy.build('auto');
  assert.deepStrictEqual(built.profiles.policy.apply_to, executionPolicy.WORKLOADS);
  assert.strictEqual(built.profiles.policy.no_cap_tier, 'medium');
  assert.deepStrictEqual(built.profiles.policy.steps, [
    { min_remaining_ratio: 0.2, tier: 'medium' },
    { min_remaining_ratio: 0, tier: 'small' },
  ]);
  assert.strictEqual(built.budget.allocation.mode, 'auto');
  assert.ok(executionPolicy.WORKLOADS.every(
    (workload) => built.budget.allocation.workloads[workload].weight === 1
  ));
});

test('節約は上限の有無にかかわらず small を使う', () => {
  const built = executionPolicy.build('saving');
  assert.strictEqual(built.profiles.policy.no_cap_tier, 'small');
  assert.deepStrictEqual(built.profiles.policy.steps, [
    { min_remaining_ratio: 0, tier: 'small' },
  ]);
  assert.strictEqual(built.budget.allocation.workloads.flow.on_exhausted, 'pause');
});

test('不正なカスタム値は部分保存せず入力エラーとして返す', () => {
  const result = executionPolicy.save({}, { mode: 'custom', custom: { tokenLimit: -1 } });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.budgetSaved, false);
  assert.strictEqual(result.errors[0].target, 'policy');
});

test('品質優先は large から残量20%で medium、5%で small へ切り替える', () => {
  const built = executionPolicy.build('quality');
  assert.strictEqual(built.profiles.policy.no_cap_tier, 'large');
  assert.deepStrictEqual(built.profiles.policy.steps, [
    { min_remaining_ratio: 0.2, tier: 'large' },
    { min_remaining_ratio: 0.05, tier: 'medium' },
    { min_remaining_ratio: 0, tier: 'small' },
  ]);
});

test('カスタムは4項目と機能別上書きを内部契約へ変換する', () => {
  const built = executionPolicy.build('custom', {
    normalTier: 'large',
    tokenLimit: 120000,
    switchTiming: 'early',
    onExhausted: 'pause',
    overrides: { flow: { priority: 'high', maxTokens: 50000, onExhausted: 'stop' } },
  });
  assert.strictEqual(built.budget.tokens, 120000);
  assert.strictEqual(built.profiles.policy.no_cap_tier, 'large');
  assert.deepStrictEqual(built.profiles.policy.steps, [
    { min_remaining_ratio: 0.3, tier: 'large' },
    { min_remaining_ratio: 0.05, tier: 'medium' },
    { min_remaining_ratio: 0, tier: 'small' },
  ]);
  assert.strictEqual(built.budget.allocation.workloads.project.weight, 1);
  assert.strictEqual(built.budget.allocation.workloads.flow.weight, 2);
  assert.strictEqual(built.budget.allocation.workloads.flow.max_tokens, 50000);
  assert.strictEqual(built.budget.allocation.workloads.flow.on_exhausted, 'stop');
});

test('単純作業はカスタムで明示した場合だけ使う', () => {
  const built = executionPolicy.build('custom', { normalTier: 'basic' });
  assert.strictEqual(built.profiles.policy.no_cap_tier, 'basic');
  assert.deepStrictEqual(built.profiles.policy.steps, [
    { min_remaining_ratio: 0, tier: 'basic' },
  ]);
  assert.notStrictEqual(executionPolicy.build('saving').profiles.policy.no_cap_tier, 'basic',
    '節約だからといって能力の足りない候補へ自動降格しない');
});

test('単一保存はbudgetとprofilesを更新してcontrolへ反映する', () => {
  const budgetDir = fs.mkdtempSync(path.join(os.tmpdir(), 'exec-policy-budget-'));
  const controlDir = fs.mkdtempSync(path.join(os.tmpdir(), 'exec-policy-control-'));
  const cfg = { orchestration: { budgetDir, controlDir } };
  profiles.save(cfg, { tiers: {
    small: { order: 1, candidates: [{ agent_cli: 'ollama', model: 'qwen' }] },
    medium: { order: 2, candidates: [{ agent_cli: 'claude', model: 'sonnet' }] },
    large: { order: 3, candidates: [{ agent_cli: 'claude', model: 'opus' }] },
  } });
  const result = executionPolicy.save(cfg, { mode: 'quality' });
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.budgetSaved, true);
  assert.strictEqual(result.profilesSaved, true);
  assert.strictEqual(result.applied, true);
  assert.strictEqual(profiles.load(cfg).executionPolicy.mode, 'quality');
  const control = JSON.parse(fs.readFileSync(path.join(controlDir, 'control.json'), 'utf8'));
  assert.strictEqual(control.workloads.flow.tier, 'large');
  assert.strictEqual(control.workloads.audit.tier, 'large');
});

console.log(`\n${passed} tests passed (execution-policy)`);
