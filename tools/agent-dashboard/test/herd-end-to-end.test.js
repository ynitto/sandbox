'use strict';

// `herd` の 1 語から control.json まで — 管理面の通し。
//
// 人が実行レベルの構成へ打つのは `herd`（モデル欄は空）だけ。そこから
//   profiles.json（宣言はそのまま）→ 実測で展開 → control.json（具体候補）
// までが繋がっていること、そして **legacy 欄に `herd` を書かない**ことを縛る。
// `load_cli("herd")` は「未知の agent_cli」で落ちるので、selection_policy を読まない
// version 1 経路の端末がそこを見ると実行できなくなる。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const REPO = path.join(__dirname, '..', '..', '..');
process.env.KIRO_AGENTS_DIR = path.join(REPO, 'agents');

const profiles = require('../src/features/orchestration/main/profiles');
const executionPolicy = require('../src/features/orchestration/main/execution-policy');

function seedQualifications(dir) {
  const q = {
    version: 1,
    revision: 1,
    evaluation_profiles: {},
    candidates: [
      candidate('aider', 'gemma4:e4b', {
        'single-symbol-edit': qualification('aider-e4b-sse-v1', 'qualified', 0.7, 61),
        'existing-test-repair': qualification('aider-e4b-etr-v1', 'qualified', 0.7, 141),
      }),
      candidate('ollama', 'gemma4:e4b', {
        extract: qualification('ollama-e4b-extract-v1', 'qualified', 0.61, 4),
        'bounded-analysis': qualification('ollama-e4b-analysis-v1', 'qualified', 0.61, 5),
        'bounded-review': qualification('ollama-e4b-review-v1', 'blocked', 0.09, 3),
      }),
      candidate('ollama', 'gemma4:12b', {
        'bounded-review': qualification('ollama-12b-review-v1', 'qualified', 0.61, 18),
      }),
      // 一族の外（クラウド）。herd の展開に混ざってはいけない。
      candidate('claude', 'sonnet', {
        'bounded-analysis': qualification('claude-sonnet-analysis-v1', 'qualified', 0.95, 20),
      }),
    ],
  };
  fs.writeFileSync(path.join(dir, 'qualifications.json'), JSON.stringify(q, null, 2));
  return q;
}

function candidate(agentCli, model, qualifications) {
  return {
    agent_cli: agentCli,
    model,
    qualifications,
    execution_site: 'device',
    economics: { estimated_cost: 0 },
  };
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
    // 期限切れで消えないよう十分先にする。
    valid_until: '2099-01-01T00:00:00Z',
    source: 'eval-archive',
  };
}

function setup({ withQualifications = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'herd-e2e-'));
  const cfg = { orchestration: { controlDir: dir, budgetDir: dir } };
  if (withQualifications) seedQualifications(dir);
  profiles.save(cfg, {
    tiers: {
      basic: { order: 0, label: '単純作業', candidates: [{ agent_cli: 'herd' }] },
      small: { order: 1, label: '軽量', candidates: [{ agent_cli: 'herd' }] },
      medium: { order: 2, label: '標準', candidates: [{ agent_cli: 'claude', model: 'sonnet' }] },
      large: { order: 3, label: '高性能', candidates: [{ agent_cli: 'claude', model: 'opus' }] },
    },
  });
  return { dir, cfg };
}

function readJson(dir, name) {
  return JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
}

test('打つのは herd の 1 語。宣言はそのまま profiles.json に残る', () => {
  const { dir } = setup();
  const saved = readJson(dir, 'profiles.json');
  assert.deepStrictEqual(saved.tiers.small.candidates, [{ agent_cli: 'herd' }]);
  assert.deepStrictEqual(saved.tiers.basic.candidates, [{ agent_cli: 'herd' }]);
});

test('実行方針の保存が通る（herd 行も「候補あり」として数える）', () => {
  const { cfg } = setup();
  const result = executionPolicy.save(cfg, { mode: 'auto' });
  assert.deepStrictEqual(result.errors, []);
  assert.strictEqual(result.ok, true);
});

test('control.json の legacy 欄に herd を書かない', () => {
  const { dir, cfg } = setup();
  executionPolicy.save(cfg, { mode: 'auto' });
  const flow = readJson(dir, 'control.json').workloads.flow;
  assert.notStrictEqual(flow.agent_cli, 'herd',
    'load_cli("herd") は未知の agent_cli で落ちる——legacy 経路が実行できなくなる');
  assert.ok(flow.agent_cli, 'legacy 欄は具体名で埋まる');
  assert.ok(flow.model, 'legacy 欄のモデルも具体値');
});

test('selection_policy の候補は実測由来の具体候補になる', () => {
  const { dir, cfg } = setup();
  executionPolicy.save(cfg, { mode: 'auto' });
  const policy = readJson(dir, 'control.json').workloads.flow.selection_policy;
  const ids = policy.candidates.map((c) => `${c.agent_cli}/${c.model}`);
  assert.ok(ids.includes('aider/gemma4:e4b'));
  assert.ok(ids.includes('ollama/gemma4:12b'));
  assert.ok(!ids.some((id) => id.startsWith('herd/')), 'herd が候補として漏れない');
});

test('用途別の順位表が実測どおりに分かれる', () => {
  const { dir, cfg } = setup();
  executionPolicy.save(cfg, { mode: 'auto' });
  const byPurpose = readJson(dir, 'control.json').workloads.flow.selection_policy.by_purpose;
  const ids = (key) => (byPurpose[key].candidates || []).map((c) => `${c.agent_cli}/${c.model}`);
  assert.deepStrictEqual(ids('work'), ['aider/gemma4:e4b']);
  assert.deepStrictEqual(ids('verify'), ['ollama/gemma4:12b']);
  assert.deepStrictEqual(ids('extract'), ['ollama/gemma4:e4b']);
  assert.deepStrictEqual(ids('planner'), [], '裏付けが無い用途は park の宣言');
});

test('12b はコード役に載らない（tier 構成ではなく実測が封じる）', () => {
  const { dir, cfg } = setup();
  executionPolicy.save(cfg, { mode: 'auto' });
  const byPurpose = readJson(dir, 'control.json').workloads.flow.selection_policy.by_purpose;
  assert.ok(!byPurpose.work.candidates.some((c) => c.model === 'gemma4:12b'));
  assert.ok(!byPurpose.generate.candidates.some((c) => c.model === 'gemma4:12b'));
});

test('herd の展開に一族の外（クラウド）は混ざらない', () => {
  const { dir, cfg } = setup();
  executionPolicy.save(cfg, { mode: 'auto' });
  const saved = readJson(dir, 'profiles.json');
  // 宣言は herd のまま。展開結果を書き戻して意図を固定しない。
  assert.deepStrictEqual(saved.tiers.small.candidates, [{ agent_cli: 'herd' }]);
  const state = saved.state.flow;
  assert.ok(state, 'state は記録される');
  assert.notStrictEqual(state.candidate && state.candidate.agent_cli, 'herd');
});

test('実測がまだ無い端末では、推測せず候補ゼロとして扱う', () => {
  const { dir, cfg } = setup({ withQualifications: false });
  // qualifications が無いので selection_policy はコンパイルされず、herd も展開できない。
  const result = executionPolicy.save(cfg, { mode: 'auto' });
  assert.deepStrictEqual(result.errors, [], 'herd 行は「候補あり」として方針保存を通す');
  const flow = readJson(dir, 'control.json').workloads.flow;
  assert.notStrictEqual(flow.agent_cli, 'herd', '推測で herd を書かない');
  assert.strictEqual(flow.selection_policy, undefined,
    '適格性が無ければ selection_policy はコンパイルされない（従来どおり）');
  // basic / small が空になっても、medium の具体候補は生きているので決定は出る。
  assert.strictEqual(flow.agent_cli, 'claude');
});

test('変種先（profile の綴り）は tier 候補として保存できない', () => {
  const { cfg } = setup();
  // 2026-08-25 の profile 統一で ollama-json.json 等の実ファイルが消えたため、
  // 「同名のドロップインが実在するか」で判定していた旧実装は空集合を返し、
  // このガードは黙って無効になっていた（12b の封じが構成では外れていた）。
  assert.throws(() => profiles.save(cfg, {
    tiers: { small: { order: 1, candidates: [{ agent_cli: 'ollama-verify', model: 'gemma4:12b' }] } },
  }), /variant（変種）先は汎用tier候補に指定できません/);
  assert.throws(() => profiles.save(cfg, {
    tiers: { small: { order: 1, candidates: [{ agent_cli: 'ollama-json', model: 'gemma4:e4b' }] } },
  }), /variant（変種）先は汎用tier候補に指定できません/);
});

test('herd は変種先ではないので保存を通る', () => {
  const { cfg, dir } = setup();
  profiles.save(cfg, {
    tiers: { small: { order: 1, candidates: [{ agent_cli: 'herd' }] } },
  });
  assert.deepStrictEqual(
    readJson(dir, 'profiles.json').tiers.small.candidates, [{ agent_cli: 'herd' }]);
});

console.log(`\n${passed} tests passed (herd-end-to-end)`);
