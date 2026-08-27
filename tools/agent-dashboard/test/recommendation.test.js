'use strict';

// おすすめ構成（agent-recommendation）の点検・差分・適用と、役割別の実効起動形。
//
// 不変条件の網でもある: **dashboard は推奨を生成しないし、適格性も書かない**
// （生成は eval の recommend.py、writer は agent-audit だけ）。

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

const recommendation = require('../src/features/orchestration/main/recommendation');
const effectiveAgents = require('../src/features/orchestration/main/effective-agents');
const herdFamily = require('../src/features/orchestration/main/herd-family');
const agents = require('../src/features/orchestration/main/agents');

function qualification(id, status = 'qualified') {
  return {
    qualification_id: id,
    status,
    evaluation_profile_id: 'extract-v1',
    samples: 6,
    passed: 6,
    timeout_rate: 0,
    success_rate_lower_bound: 0.61,
    p50_seconds: 4,
    critical_failure_risk: 0,
    constraints: {},
    source: 'eval-archive',
    last_evaluated_at: '2026-08-26T00:00:00Z',
    valid_until: '2099-01-01T00:00:00Z',
  };
}

function document() {
  return {
    version: 1,
    revision: 1,
    generated_at: '2026-08-26T00:00:00Z',
    source: { kind: 'eval-archive' },
    tiers: {
      basic: { order: 0, label: '単純作業', candidates: [{ agent_cli: 'herd' }] },
      small: { order: 1, label: '軽量', candidates: [{ agent_cli: 'herd' }] },
      medium: { order: 2, label: '標準', candidates: [], slots: [{ requires: 'cloud-standard' }] },
      large: { order: 3, label: '高性能', candidates: [], slots: [{ requires: 'cloud-premium' }] },
    },
    execution_policy: { mode: 'auto' },
    control: { workloads: { flow: { concurrency: { max_runs: 1, workers: 1 } } } },
    qualifications: {
      version: 1,
      revision: 1,
      evaluation_profiles: {
        'extract-v1': {
          operation_class: 'extract', min_samples: 6, min_pass_rate: 0.9,
          max_timeout_rate: 0.1, window_days: 90, valid_for_days: 90,
        },
      },
      candidates: [{
        agent_cli: 'ollama', model: 'gemma4:e4b',
        execution_site: 'device', economics: { estimated_cost: 0 },
        qualifications: { extract: qualification('ollama-e4b-extract-v1') },
      }],
    },
    herd: {
      members: ['aider', 'ollama', 'opencode'],
      expansion: [
        { agent_cli: 'ollama', model: 'gemma4:e4b', qualified_for: ['extract'], usable: true },
        { agent_cli: 'aider', model: 'gemma4:12b', qualified_for: [], usable: false },
      ],
    },
    requires: { entrypoint: 'agent-herd', models: ['gemma4:e4b'] },
    evidence: [],
  };
}

function setup() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rec-ui-'));
  const home = path.join(dir, 'home');
  fs.mkdirSync(home, { recursive: true });
  const file = path.join(home, 'recommendation.json');
  fs.writeFileSync(file, JSON.stringify(document(), null, 2));
  const cfg = {
    orchestration: { controlDir: dir, budgetDir: dir, recommendationFile: file },
  };
  return { dir, cfg, file };
}

function assertVariantResolution(row, { base, profile }) {
  const legacyName = `${base}-${profile}`;
  const profileResolved = row.agent_cli === base && row.profile === profile;
  const legacyResolved = row.agent_cli === legacyName && !row.profile;
  assert.ok(profileResolved || legacyResolved,
    `${legacyName} は ${base} + profile または後方互換の実体定義として解決します`);
}

test('推奨を読む（version が違えば読まない）', () => {
  const { cfg, file } = setup();
  assert.strictEqual(recommendation.load(cfg).exists, true);
  const broken = { ...document(), version: 2 };
  fs.writeFileSync(file, JSON.stringify(broken));
  assert.strictEqual(recommendation.load(cfg).exists, false, '未知 version は推測で読まない');
});

test('点検は足りないものを言うだけで、埋めない', () => {
  const { cfg } = setup();
  const rows = recommendation.preflight(cfg, document());
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.ok(byId['agent-defs'].ok, 'この木では一族が解決できる');
  assert.ok(byId.evidence.ok);
  assert.strictEqual(byId.models.remedy, 'ollama pull gemma4:e4b',
    'pull は人の手に残す（dashboard は ollama を叩かない）');
  assert.ok(byId.models.advisory, 'モデルの取得は適用を止めない');
});

test('クラウド枠の選択肢に一族は出ない', () => {
  const { cfg } = setup();
  const choices = recommendation.cloudChoices(cfg);
  assert.ok(choices.includes('claude'));
  for (const member of herdFamily.members(cfg)) {
    assert.ok(!choices.includes(member), `${member} は枠の選択肢ではない`);
  }
  for (const variant of agents.variantTargetNames(cfg)) {
    assert.ok(!choices.includes(variant), `${variant} は内部 variant なので枠の選択肢ではない`);
  }
  assert.ok(!choices.includes('herd'));
});

test('未設定の端末では全部が変更として出る', () => {
  const { cfg } = setup();
  const rows = recommendation.diff(cfg, document());
  const changed = rows.filter((r) => r.changed).map((r) => r.key);
  assert.ok(changed.includes('tier:basic'));
  assert.ok(changed.includes('policy'));
  assert.ok(changed.includes('qualifications'));
});

test('枠は「人が選ぶ場所」なので、選んでいなければ変更にしない', () => {
  const { cfg } = setup();
  const rows = recommendation.diff(cfg, document());
  const medium = rows.find((r) => r.key === 'tier:medium');
  assert.strictEqual(medium.changed, false, '選択が無い枠は差分ではない');
  assert.strictEqual(medium.slot, 'cloud-standard');
  const chosen = recommendation.diff(cfg, document(), {
    medium: { agent_cli: 'claude', model: 'sonnet' },
  }).find((r) => r.key === 'tier:medium');
  assert.strictEqual(chosen.changed, true);
  assert.strictEqual(chosen.to, 'claude:sonnet');
});

test('枠が空のまま適用しようとしたら、保存の途中でなく先に止める', () => {
  const { cfg } = setup();
  const result = recommendation.apply(cfg, { seed: false });
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.errors[0].step, 'slots');
  assert.ok(result.errors[0].message.includes('標準'));
  assert.deepStrictEqual(result.steps, [], '1 つも書いていない');
});

test('枠を埋めれば順番どおりに適用される（方針の順序の罠を踏まない）', () => {
  const { cfg, dir } = setup();
  const result = recommendation.apply(cfg, {
    slotChoices: {
      medium: { agent_cli: 'claude', model: 'sonnet' },
      large: { agent_cli: 'claude', model: 'opus' },
    },
    seed: false,
  });
  assert.deepStrictEqual(result.errors, []);
  assert.deepStrictEqual(result.steps.map((s) => s.step),
    ['tiers', 'policy', 'concurrency', 'compile']);
  const saved = JSON.parse(fs.readFileSync(path.join(dir, 'profiles.json'), 'utf8'));
  assert.deepStrictEqual(saved.tiers.small.candidates, [{ agent_cli: 'herd' }],
    '宣言は herd の 1 語のまま');
  assert.deepStrictEqual(saved.tiers.medium.candidates,
    [{ agent_cli: 'claude', model: 'sonnet' }]);
  const control = JSON.parse(fs.readFileSync(path.join(dir, 'control.json'), 'utf8'));
  assert.deepStrictEqual(control.workloads.flow.concurrency, { max_runs: 1, workers: 1 });
});

test('適格性は agent-audit を起こすだけ（dashboard が書かない）', () => {
  const { cfg, dir } = setup();
  const calls = [];
  const result = recommendation.apply(cfg, {
    slotChoices: {
      medium: { agent_cli: 'claude', model: 'sonnet' },
      large: { agent_cli: 'claude', model: 'opus' },
    },
    runSeed: (_cfg, file) => {
      calls.push(file);
      return { ok: true, summary: { applied: true } };
    },
  });
  assert.deepStrictEqual(result.errors, []);
  assert.strictEqual(calls.length, 1, 'agent-audit を 1 回起こす');
  assert.ok(calls[0].endsWith('recommendation.json'), '推奨のパスを渡すだけ');
  assert.ok(!fs.existsSync(path.join(dir, 'qualifications.json')),
    'dashboard は qualifications.json を書かない');
});

test('agent-audit が拒否したら適用も失敗として返す', () => {
  const { cfg } = setup();
  const result = recommendation.apply(cfg, {
    slotChoices: {
      medium: { agent_cli: 'claude', model: 'sonnet' },
      large: { agent_cli: 'claude', model: 'opus' },
    },
    runSeed: () => ({ ok: false, error: '本番 receipt 由来の適格性があります' }),
  });
  assert.strictEqual(result.ok, false);
  assert.ok(result.errors.some((e) => e.step === 'seed'));
});

// --- 役割別の実効起動形 ------------------------------------------------------

test('verify が 12b に化けることが表に出る（画面と実行の食い違いの正体）', () => {
  const entry = effectiveAgents.effectiveFor({}, 'ollama');
  assert.strictEqual(entry.model, 'gemma4:e4b');
  const verify = entry.rows.find((r) => r.purpose === 'verify');
  assertVariantResolution(verify, { base: 'ollama', profile: 'verify' });
  assert.strictEqual(verify.model, 'gemma4:12b');
  assert.strictEqual(verify.model_swapped, true, 'モデルが変わることを明示する');
});

test('人が明示したモデルは変種の既定より優先される（エンジンと同じ規則）', () => {
  const entry = effectiveAgents.effectiveFor({}, 'ollama', { model: 'custom-model' });
  const verify = entry.rows.find((r) => r.purpose === 'verify');
  assert.strictEqual(verify.model, 'custom-model');
  assert.strictEqual(verify.model_swapped, false);
});

test('aider の split / retrieve / verify はそれぞれ別の起動形へ振り替わる', () => {
  const entry = effectiveAgents.effectiveFor({}, 'aider');
  const split = entry.rows.find((r) => r.purpose === 'split');
  assertVariantResolution(split, { base: 'ollama', profile: 'list-thinking' });

  // retrieve を base のまま走らせると read tool を失う（ollama 側と同じ事情）。
  const retrieve = entry.rows.find((r) => r.purpose === 'retrieve');
  assertVariantResolution(retrieve, { base: 'ollama', profile: 'read' });

  // verify を宣言しないと、作業した aider 自身が自分を採点する（仕様 §3.4 の
  // 「最も弱い構成」）。ollama と同じく 12b の検証専用変種へ振り替える。
  const verify = entry.rows.find((r) => r.purpose === 'verify');
  assertVariantResolution(verify, { base: 'ollama', profile: 'verify' });
  assert.strictEqual(verify.model, 'gemma4:12b');
  assert.strictEqual(verify.model_swapped, true, 'モデルが変わることを明示する');
});

test('herd は一族へ展開してから表を作る', () => {
  const cfg = {};
  const rows = effectiveAgents.effectiveTable(cfg, {
    small: { order: 1, candidates: [{ agent_cli: 'herd' }] },
  }, { herdMembers: herdFamily.members(cfg) });
  const names = rows.map((r) => r.agent_cli).sort();
  assert.deepStrictEqual(names, ['aider', 'ollama']);
});

test('未知の綴りは表に出さない（推測で行を作らない）', () => {
  assert.strictEqual(effectiveAgents.effectiveFor({}, 'not-a-cli').known, false);
  assert.deepStrictEqual(effectiveAgents.effectiveFor({}, '').rows, []);
});

console.log(`\n${passed} tests passed (recommendation)`);
