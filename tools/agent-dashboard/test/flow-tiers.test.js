'use strict';

// coherence: code=tools/agent-dashboard/src/features/orchestration/main/flow-tiers.js, doc=docs/plans/2026-08-12-agent-flow-tier-eligibility-strategy-design.md

// agent-flow の機能・役割ごとの実行可能レベル（tier）カタログと、実行方針（戦略）に応じた
// 自動 tier 決定の単体テスト。追加依存なしで `node test/flow-tiers.test.js` で走る。

const assert = require('assert');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const flowTiers = require('../src/features/orchestration/main/flow-tiers');
const adhoc = require('../src/features/adhoc-flow/main/adhoc');

test('カタログは human を除く全ノード機能を覆い、実行可能レベルは標準語彙の連続区間', () => {
  const kinds = Object.keys(flowTiers.KIND_MIN_TIER);
  assert.deepStrictEqual(
    [...kinds].sort(),
    adhoc.NODE_KINDS.filter((kind) => kind !== 'human').sort(),
    'エディタの機能一覧とカタログがずれている'
  );
  for (const kind of kinds) {
    const tiers = flowTiers.allowedTiers(kind);
    assert.ok(tiers.length >= 1, `${kind} に実行可能レベルがない`);
    const start = flowTiers.TIER_ORDER.indexOf(tiers[0]);
    assert.deepStrictEqual(tiers, flowTiers.TIER_ORDER.slice(start),
      `${kind} の実行可能レベルが下限からの連続区間でない`);
    assert.ok(tiers.includes('large'), `${kind} は上限を切らない（高性能は常に適格）`);
  }
});

test('単純作業（basic）は一手順で完結する機能だけに任せる', () => {
  const basicKinds = Object.keys(flowTiers.KIND_MIN_TIER)
    .filter((kind) => flowTiers.allowedTiers(kind).includes('basic'));
  assert.deepStrictEqual(basicKinds.sort(), ['classify', 'extract', 'filter', 'map']);
  // 成果物・横断判断・フロー形状・検証・読解・集約は basic に任せない
  for (const kind of ['work', 'generate', 'verify', 'retrieve', 'reduce']) {
    assert.strictEqual(flowTiers.allowedTiers(kind)[0], 'small', `${kind} は軽量以上`);
  }
  for (const kind of ['synthesize', 'judge', 'split']) {
    assert.strictEqual(flowTiers.allowedTiers(kind)[0], 'medium', `${kind} は標準以上`);
  }
});

test('役割（planner/evaluator）は run の形と継続を決めるため標準以上', () => {
  assert.strictEqual(flowTiers.ROLE_MIN_TIER.planner, 'medium');
  assert.strictEqual(flowTiers.ROLE_MIN_TIER.evaluator, 'medium');
  // worker/verify は対応する kind と同じ下限（カタログ内で矛盾しない）
  assert.strictEqual(flowTiers.ROLE_MIN_TIER.worker, flowTiers.KIND_MIN_TIER.work);
  assert.strictEqual(flowTiers.ROLE_MIN_TIER.verify, flowTiers.KIND_MIN_TIER.verify);
});

test('文章検証は12b候補を軽量へ載せるがコードworkerには載せない', () => {
  const catalog = flowTiers.catalog();
  assert.deepStrictEqual(catalog.roles.verify.candidates, [
    { agent_cli: 'ollama-verify', model: 'gemma4:12b', tier: 'small' },
  ]);
  assert.deepStrictEqual(flowTiers.seedTierCandidates().basic, [
    { agent_cli: 'aider', model: 'gemma4:e4b' },
    { agent_cli: 'ollama', model: 'gemma4:e4b' },
  ]);
  assert.deepStrictEqual(flowTiers.seedTierCandidates().small, [
    { agent_cli: 'ollama-verify', model: 'gemma4:12b' },
  ]);
  assert.ok(!flowTiers.seedTierCandidates().basic.some(
    (candidate) => candidate.agent_cli === 'aider' && candidate.model === 'gemma4:12b'
  ));
});

test('オプションとして拡張する振る舞いは下限を引き上げる', () => {
  // classify 単体は basic 可、route（分類結果でフローの形が決まる）は small 以上
  assert.deepStrictEqual(flowTiers.allowedTiers('classify'),
    ['basic', 'small', 'medium', 'large']);
  assert.deepStrictEqual(flowTiers.allowedTiers('classify', { continuation: 'route' }),
    ['small', 'medium', 'large']);
  // verify 単体は small 可、retry（判定がリトライ予算と再作業を駆動）は medium 以上
  assert.deepStrictEqual(flowTiers.allowedTiers('verify'), ['small', 'medium', 'large']);
  assert.deepStrictEqual(flowTiers.allowedTiers('verify', { continuation: 'retry' }),
    ['medium', 'large']);
  // カタログ（IPC で UI へ渡す形）もオプションの下限を宣言する
  const catalog = flowTiers.catalog();
  assert.strictEqual(catalog.kinds.classify.options.route, 'small');
  assert.strictEqual(catalog.kinds.verify.options.retry, 'medium');
  assert.deepStrictEqual(catalog.order, flowTiers.TIER_ORDER);
});

test('未知の機能・独自レベル名は制限しない（加算的契約）', () => {
  assert.deepStrictEqual(flowTiers.allowedTiers('future-kind'), flowTiers.TIER_ORDER);
  assert.strictEqual(flowTiers.isKnownTier('economy'), false);
  assert.strictEqual(flowTiers.isKnownTier('medium'), true);
});

test('clampTier は範囲外を端へ丸め、方針の性格で方向を決める', () => {
  const allowed = ['medium', 'large'];
  assert.strictEqual(flowTiers.clampTier(allowed, 'small', 'saving'), 'medium');
  assert.strictEqual(flowTiers.clampTier(allowed, 'large', 'quality'), 'large');
  assert.strictEqual(flowTiers.clampTier(allowed, 'medium', 'auto'), 'medium');
  // （理論上の）飛び穴: 節約は下へ、それ以外は上へ
  assert.strictEqual(flowTiers.clampTier(['basic', 'medium', 'large'], 'small', 'saving'), 'basic');
  assert.strictEqual(flowTiers.clampTier(['basic', 'medium', 'large'], 'small', 'auto'), 'medium');
});

test('decideAutoTier: 方針の全段が適格なら継承し、不適格な段を選びうるなら固定する', () => {
  // work は small..large すべて適格 → どの方針でも継承
  const inherit = flowTiers.decideAutoTier({
    kind: 'work', mode: 'saving',
    policyTiers: ['small'], controlTier: 'small',
  });
  assert.strictEqual(inherit.inherit, true);

  // judge は medium 以上 → 節約方針（small を選びうる）では medium へ固定
  const pinned = flowTiers.decideAutoTier({
    kind: 'judge', mode: 'saving',
    policyTiers: ['small'], controlTier: 'small',
  });
  assert.strictEqual(pinned.inherit, false);
  assert.strictEqual(pinned.tier, 'medium');
  assert.match(pinned.reason, /strategy=saving/);

  // 品質優先で今の段が large なら judge は large のまま固定
  const quality = flowTiers.decideAutoTier({
    kind: 'judge', mode: 'quality',
    policyTiers: ['large', 'medium', 'small'], controlTier: 'large',
  });
  assert.strictEqual(quality.inherit, false);
  assert.strictEqual(quality.tier, 'large');

  // control に段がまだ無い端末は方針の既定（カスタムは normalTier）から決める
  const custom = flowTiers.decideAutoTier({
    kind: 'judge', mode: 'custom', custom: { normalTier: 'small' },
    policyTiers: ['small'], controlTier: '',
  });
  assert.strictEqual(custom.tier, 'medium');

  // 独自レベル名の段しか無い方針は適格性の対象外として継承（後方互換）
  const legacy = flowTiers.decideAutoTier({
    kind: 'judge', mode: 'auto',
    policyTiers: ['economy'], controlTier: 'economy',
  });
  assert.strictEqual(legacy.inherit, true);
});

console.log(`\n${passed} tests passed`);
