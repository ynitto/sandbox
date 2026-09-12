'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const settings = require('../src/main/settings');
const store = require('../src/main/store');

test('旧設定のエージェントとモデルを3つのTierへ引き継ぐ', () => {
  const normalized = settings.normalize({ lastCli: 'codex', lastModel: 'gpt-5' });
  assert.deepStrictEqual(normalized.execution.tiers, {
    small: { cli: 'codex', model: 'gpt-5' },
    medium: { cli: 'codex', model: 'gpt-5' },
    large: { cli: 'codex', model: 'gpt-5' },
  });
});

test('起動方針が対応するTierのエージェントとモデルを選ぶ', () => {
  const config = settings.normalize({ execution: { tiers: {
    small: { cli: 'aider', model: 'local' },
    medium: { cli: 'codex', model: 'standard' },
    large: { cli: 'claude', model: 'quality' },
  } } });
  assert.deepStrictEqual(settings.resolve(config, { policy: 'saving' }), {
    policy: 'saving', tier: 'small', cli: 'aider', model: 'local', source: 'policy',
  });
  assert.deepStrictEqual(settings.resolve(config, { policy: 'recommended' }), {
    policy: 'recommended', tier: 'medium', cli: 'codex', model: 'standard', source: 'policy',
  });
  assert.deepStrictEqual(settings.resolve(config, { policy: 'quality' }), {
    policy: 'quality', tier: 'large', cli: 'claude', model: 'quality', source: 'policy',
  });
});

test('UIで扱う共通指示と実行制御を安全な設定値へ揃える', () => {
  const normalized = settings.normalize({
    lastCli: 'codex', lastModel: '', lastReadonly: true,
    instructions: {
      enabled: false,
      text: '  日本語で回答する  ',
      skills: ['ui-designer', 'ui-designer', '', ' self-checking '],
      startupActions: [
        { type: 'skill', value: ' brainstorming ', onError: 'fail' },
        { type: 'command', value: ' npm test ', onError: 'invalid' },
        { type: 'unknown', value: 'ignored' },
      ],
    },
    execution: { defaultPolicy: 'quality', defaultReadonly: false, defaultAutoApprove: true, maxConcurrent: 20 },
  });
  assert.deepStrictEqual(normalized.instructions, {
    enabled: false,
    text: '日本語で回答する',
    forkEnabled: true,
    skills: ['ui-designer', 'self-checking'],
    skillSelection: {
      enabled: true,
      defaultMode: 'auto',
      candidates: ['ui-designer', 'self-checking'],
    },
    startupActions: [
      { type: 'skill', value: 'brainstorming', onError: 'fail' },
      { type: 'command', value: 'npm test', onError: 'warn' },
    ],
    // 定型の依頼を保存していない設定は、既定の 2 つを持つ
    quickRequests: settings.DEFAULT_QUICK_REQUESTS,
  });
  assert.strictEqual(normalized.execution.defaultPolicy, 'quality');
  assert.strictEqual(normalized.execution.defaultReadonly, false);
  assert.strictEqual(normalized.execution.defaultAutoApprove, true);
  assert.strictEqual(normalized.execution.maxConcurrent, 8);
});

test('既存config.jsonを移行しながら未知の設定を保持して保存する', () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-settings-'));
  fs.writeFileSync(path.join(userData, 'config.json'), JSON.stringify({
    lastCli: 'claude', lastModel: 'sonnet', customRootKey: { keep: true },
    execution: { futureOption: 'keep-me', tiers: { medium: { model: 'medium-model', futureTierOption: 'keep-tier' } } },
  }));
  const loaded = store.loadConfig(userData);
  assert.strictEqual(loaded.execution.tiers.medium.cli, 'claude');
  const saved = store.saveConfig(userData, { execution: { defaultPolicy: 'saving' } });
  assert.strictEqual(saved.execution.defaultPolicy, 'saving');
  assert.strictEqual(saved.execution.tiers.large.model, 'sonnet');
  assert.strictEqual(saved.execution.futureOption, 'keep-me');
  assert.strictEqual(saved.execution.tiers.medium.futureTierOption, 'keep-tier');
  assert.deepStrictEqual(saved.customRootKey, { keep: true });
  const partial = store.saveConfig(userData, { execution: { tiers: { medium: { cli: 'codex' } } } });
  assert.deepStrictEqual(partial.execution.tiers.medium, {
    cli: 'codex', model: 'medium-model', futureTierOption: 'keep-tier',
  });
});

test('直接指定を方針より優先し、指定がなければ既定方針を使う', () => {
  const config = settings.normalize({
    execution: {
      defaultPolicy: 'quality',
      tiers: { large: { cli: 'claude', model: 'opus' } },
    },
  });
  assert.deepStrictEqual(settings.resolve(config, { policy: 'direct', cli: 'codex', model: 'gpt' }), {
    policy: 'direct', tier: '', cli: 'codex', model: 'gpt', source: 'direct',
  });
  assert.deepStrictEqual(settings.resolve(config), {
    policy: 'quality', tier: 'large', cli: 'claude', model: 'opus', source: 'policy',
  });
});

test('旧推奨スキルを自動選択の候補へ移行する', () => {
  const normalized = settings.normalize({ instructions: { skills: ['ui-designer', 'self-checking'] } });
  assert.deepStrictEqual(normalized.instructions.skillSelection, {
    enabled: true,
    defaultMode: 'auto',
    candidates: ['ui-designer', 'self-checking'],
  });
});

test('エージェントを最適化する: OFF か herd が無ければ、節約 / 品質重視は「おすすめ」として解決する', () => {
  const config = settings.normalize({ execution: { defaultPolicy: 'quality', tiers: {
    small: { cli: 'aider', model: 'local' }, medium: { cli: 'codex', model: 'standard' }, large: { cli: 'claude', model: 'quality' },
  } } });
  assert.strictEqual(config.execution.optimizeAgents, true, '既定は ON');
  assert.strictEqual(settings.normalize({ execution: { optimizeAgents: false } }).execution.optimizeAgents, false);
  assert.strictEqual(settings.optimized(config, { herdAvailable: true }), true);
  assert.strictEqual(settings.optimized(config, { herdAvailable: false }), false, 'herd が無ければ効かない');
  assert.strictEqual(settings.optimized(settings.normalize({ execution: { optimizeAgents: false } }), { herdAvailable: true }), false, 'OFF なら herd があっても効かない');
  assert.strictEqual(settings.effectivePolicy('saving', { optimized: false }), 'recommended');
  assert.strictEqual(settings.effectivePolicy('quality', { optimized: false }), 'recommended');
  assert.strictEqual(settings.effectivePolicy('recommended', { optimized: false }), 'recommended');
  assert.strictEqual(settings.effectivePolicy('direct', { optimized: false }), 'direct', '直接指定（カスタム）は残る');
  assert.strictEqual(settings.effectivePolicy('saving', { optimized: true }), 'saving');
  assert.deepStrictEqual(settings.resolve(config, { policy: 'saving' }, { optimized: false }), {
    policy: 'recommended', tier: 'medium', cli: 'codex', model: 'standard', source: 'policy',
  });
  assert.strictEqual(settings.resolve(config, {}, { optimized: false }).policy, 'recommended', '既定が品質重視でも おすすめ');
  assert.strictEqual(settings.resolve(config, { policy: 'quality' }).policy, 'quality', '省略時は従来どおり');
  assert.strictEqual(settings.resolve(config, { policy: 'direct', cli: 'kiro' }, { optimized: false }).cli, 'kiro');
});

test('定型の依頼は上限3つで、文面の無い行を捨てる', () => {
  const normalized = settings.normalize({
    instructions: {
      quickRequests: [
        { label: ' コミットする ', text: ' 変更をコミットして ' },
        { label: '', text: 'テストを実行して\n結果を要約して' },
        { label: '本文なし', text: '   ' },
        'おかしな値',
        { label: '4つ目', text: '溢れる' },
        { label: '5つ目', text: '溢れる' },
      ],
    },
  });
  assert.deepStrictEqual(normalized.instructions.quickRequests, [
    { label: 'コミットする', text: '変更をコミットして' },
    // ボタンの文字が無ければ本文の 1 行目を使う
    { label: 'テストを実行して', text: 'テストを実行して\n結果を要約して' },
    { label: '4つ目', text: '溢れる' },
  ]);
  // 「出さない」は空の配列で表す（既定へは戻らない）
  assert.deepStrictEqual(settings.normalize({ instructions: { quickRequests: [] } }).instructions.quickRequests, []);
});

test('前面に無いときの通知は既定でON', () => {
  assert.strictEqual(settings.normalize(null).notify.background, true);
  assert.strictEqual(settings.normalize({ notify: {} }).notify.background, true);
  assert.strictEqual(settings.normalize({ notify: { background: false } }).notify.background, false);
});
