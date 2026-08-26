'use strict';

// 用途別の候補選択（by_purpose）— カタログと Compiler 側の契約。
//
// 実測は最初から `候補 × operation_class` の形を持っている。Compiler がその次元を
// 捨てると、Resolver の自動選択は `remaining[0]`（workload ごとに 1 位が全ノードを取る）
// になり、抽出の実績しかない候補がレビューにも 1 位で選ばれる。ここはその回帰の網。

const assert = require('assert');
const compiler = require('../src/features/orchestration/main/execution-policy-compiler');
const purposeOperations = require('../src/features/orchestration/main/purpose-operations');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function qualification(id, status, lower, p50) {
  return {
    qualification_id: id,
    status,
    samples: 9,
    passed: Math.round(lower * 9),
    success_rate_lower_bound: lower,
    critical_failure_risk: status === 'blocked' ? 1 : 0,
    p50_seconds: p50,
    expected_calls_with_failures: lower > 0 ? 1 / lower : null,
    last_evaluated_at: '2026-08-14T00:00:00Z',
    valid_until: '2026-11-12T00:00:00Z',
    source: 'eval-archive',
  };
}

// 現 archive の seed と同じ形（B2 修正後＝12b も正典名 `ollama`）。
function fixture() {
  return {
    version: 1,
    revision: 3,
    evaluation_profiles: {
      'extract-v1': { operation_class: 'extract' },
      'bounded-review-v1': { operation_class: 'bounded-review' },
    },
    candidates: [
      {
        agent_cli: 'aider',
        model: 'gemma4:e4b',
        economics: { estimated_cost: 0 },
        qualifications: {
          'single-symbol-edit': qualification('aider-e4b-sse-v1', 'qualified', 0.7, 61),
          'existing-test-repair': qualification('aider-e4b-etr-v1', 'qualified', 0.7, 141),
          'bounded-review': qualification('aider-e4b-review-v1', 'blocked', 0, 200),
        },
      },
      {
        agent_cli: 'ollama',
        model: 'gemma4:e4b',
        economics: { estimated_cost: 0 },
        qualifications: {
          extract: qualification('ollama-e4b-extract-v1', 'qualified', 0.61, 4),
          'bounded-review': qualification('ollama-e4b-review-v1', 'blocked', 0.1, 3),
        },
      },
      {
        agent_cli: 'ollama',
        model: 'gemma4:12b',
        economics: { estimated_cost: 0 },
        qualifications: {
          'bounded-review': qualification('ollama-12b-review-v1', 'qualified', 0.61, 18),
          extract: qualification('ollama-12b-extract-v1', 'trial', 0.3, 200),
        },
      },
    ],
  };
}

const tiers = {
  basic: { order: 0, candidates: [{ agent_cli: 'ollama', model: 'gemma4:e4b' }] },
  small: {
    order: 1,
    candidates: [
      { agent_cli: 'aider', model: 'gemma4:e4b' },
      { agent_cli: 'ollama', model: 'gemma4:12b' },
    ],
  },
  medium: { order: 2, candidates: [{ agent_cli: 'claude', model: 'sonnet' }] },
};

function compile(extra = {}) {
  return compiler.compileSelectionPolicy({
    strategy: 'balanced', tiers, tierCeiling: 'medium', qualifications: fixture(),
    nowMs: Date.parse('2026-09-01T00:00:00Z'), ...extra,
  });
}

function ids(entry) {
  return (entry.candidates || []).map((c) => `${c.agent_cli}/${c.model}`);
}

test('用途ごとに、その処理種別を裏付ける候補だけが順位表へ載る', () => {
  const policy = compile();
  // コード編集の実測を持つのは aider だけ。
  assert.deepStrictEqual(ids(policy.by_purpose.work), ['aider/gemma4:e4b']);
  // レビューの実測を持つのは 12b だけ（e4b は blocked、aider も blocked）。
  assert.deepStrictEqual(ids(policy.by_purpose.verify), ['ollama/gemma4:12b']);
  // 抽出は e4b が qualified、12b は trial。
  assert.deepStrictEqual(ids(policy.by_purpose.extract),
    ['ollama/gemma4:e4b', 'ollama/gemma4:12b']);
});

test('12b はコード役へ、e4b はレビュー役へ、実測が無いので載らない', () => {
  const policy = compile();
  assert.ok(!ids(policy.by_purpose.work).includes('ollama/gemma4:12b'),
    '12b をコード worker へ流さない封じは、tier 構成ではなく実測が担う');
  assert.ok(!ids(policy.by_purpose.verify).includes('ollama/gemma4:e4b'),
    'bounded-review が blocked の候補はレビュー役に載らない');
});

test('trial 裏付けだけの候補は status: trial を保つ', () => {
  const policy = compile();
  const trial = policy.by_purpose.extract.candidates
    .find((c) => c.model === 'gemma4:12b');
  assert.strictEqual(trial.status, 'trial');
  const qualified = policy.by_purpose.extract.candidates
    .find((c) => c.model === 'gemma4:e4b');
  assert.strictEqual(qualified.status, undefined, 'qualified は無印のまま');
});

test('qualification_refs は要求した処理種別のぶんだけに絞る', () => {
  const policy = compile();
  assert.deepStrictEqual(policy.by_purpose.work.candidates[0].qualification_refs,
    ['aider-e4b-etr-v1', 'aider-e4b-sse-v1']);
  // 全部の裏付けを並べる flat な candidates とは違う。
  const flat = policy.candidates.find((c) => c.agent_cli === 'aider');
  assert.ok(flat.qualification_refs.length >= 2);
});

test('裏付けが 1 つも無い用途は、空の宣言として残す（park の材料）', () => {
  const policy = compile();
  assert.ok('planner' in policy.by_purpose, 'カタログにある用途は必ず現れる');
  assert.deepStrictEqual(policy.by_purpose.planner.candidates, []);
  assert.deepStrictEqual(policy.by_purpose.planner.operations, ['planner']);
});

test('operations はカタログの宣言をそのまま運ぶ', () => {
  const policy = compile();
  assert.deepStrictEqual(policy.by_purpose.work.operations,
    purposeOperations.operationsFor('work'));
  assert.deepStrictEqual(policy.by_purpose.verify.operations, ['bounded-review']);
});

test('flat な candidates は従来どおり（additive であること）', () => {
  const policy = compile();
  assert.deepStrictEqual(policy.candidates.map((c) => `${c.agent_cli}/${c.model}`),
    ['aider/gemma4:e4b', 'ollama/gemma4:e4b', 'ollama/gemma4:12b']);
  assert.strictEqual(policy.strategy, 'balanced');
  assert.strictEqual(policy.no_candidate, 'park');
});

test('処理種別は qualifications の鍵から読む（qualification 本体は持たない）', () => {
  // 契約上 operation_class を持つのは evaluation_profile の側で、qualification には無い。
  // 鍵を拾い損ねると by_purpose が全部空になる。
  const document = fixture();
  for (const candidate of document.candidates) {
    for (const qualification of Object.values(candidate.qualifications)) {
      assert.strictEqual(qualification.operation_class, undefined);
    }
  }
  assert.ok(compile().by_purpose.verify.candidates.length > 0);
});

test('候補が 1 件も無い実行レベルでは by_purpose を出さない', () => {
  const policy = compiler.compileSelectionPolicy({
    strategy: 'balanced', tiers, tierCeiling: 'not-found', qualifications: fixture(),
  });
  assert.deepStrictEqual(policy.candidates, []);
  assert.strictEqual(policy.by_purpose, undefined,
    '空の順位表しか作れないときは宣言ごと出さない（park の宣言と紛れないように）');
});

test('カタログの値はすべて空でない文字列配列', () => {
  for (const purpose of purposeOperations.knownPurposes()) {
    const operations = purposeOperations.operationsFor(purpose);
    assert.ok(Array.isArray(operations) && operations.length, `${purpose} の宣言が空`);
    assert.ok(operations.every((op) => typeof op === 'string' && op.trim()),
      `${purpose} に空の処理種別`);
  }
  assert.strictEqual(purposeOperations.operationsFor('does-not-exist'), null);
  assert.strictEqual(purposeOperations.operationsFor(''), null);
});

console.log(`\n${passed} tests passed (purpose-operations)`);
