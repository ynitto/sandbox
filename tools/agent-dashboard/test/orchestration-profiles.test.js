'use strict';

// 実行プロファイル自動選択（agent-profiles 契約）のテスト。
// - decide(): 純関数の性質（決定性・境界値・ヒステリシス・最小保持・quota フォールバック）
// - load/save: 正規化（不正値は黙って落とす）・policy のフィールド単位マージ（state は触らない）
// - apply(): 現状と一致する決定は control.json / profiles.json を書かない（revision を上げない）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profilesMod = require('../src/features/orchestration/main/profiles');
const control = require('../src/features/orchestration/main/control');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function cfgFor(controlDir, budgetDir) {
  return { orchestration: { controlDir, budgetDir: budgetDir || controlDir } };
}

// decide() へ渡す最小限の profiles / usage オブジェクトを組み立てる（loadProfiles と
// 同じ正規化済み形。ファイル I/O を経由せず純関数のロジックだけを縛る）。
function makeTiers() {
  return {
    large: { order: 3, label: '大', candidates: [{ agent_cli: 'claude', model: 'opus' }] },
    medium: { order: 2, label: '中', candidates: [{ agent_cli: 'claude', model: 'sonnet' }] },
    small: { order: 1, label: '小', candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] },
  };
}

function makePolicy(overrides) {
  return {
    apply_to: ['flow'],
    steps: [
      { min_remaining_ratio: 0.5, tier: 'large' },
      { min_remaining_ratio: 0.2, tier: 'medium' },
      { min_remaining_ratio: 0.0, tier: 'small' },
    ],
    no_cap_tier: 'large',
    hysteresis: 0.05,
    min_hold_sec: 900,
    interval_sec: 300,
    ...overrides,
  };
}

function makeUsage(cap, totalTokens, agentsUsage) {
  return {
    workloads: { flow: { tokenCap: cap, totalTokens } },
    agents: agentsUsage || {},
    config: { allocation: { agents: {} } },
  };
}

// --- decide(): 純関数の性質 --------------------------------------------------

test('decide: 同じ入力なら同じ出力（決定的）', () => {
  const profiles = { enabled: true, tiers: makeTiers(), policy: makePolicy(), state: {} };
  const usage = makeUsage(1000, 400); // remaining = 0.6 → large
  const a = profilesMod.decide(profiles, usage, 1000000);
  const b = profilesMod.decide(profiles, usage, 1000000);
  assert.deepStrictEqual(a, b);
  assert.strictEqual(a.flow.tier, 'large');
  assert.strictEqual(a.flow.candidate.agent_cli, 'claude');
  assert.strictEqual(a.flow.candidate.model, 'opus');
});

test('decide: 境界値は満たす側（remaining == min_remaining_ratio）', () => {
  const profiles = { enabled: true, tiers: makeTiers(), policy: makePolicy(), state: {} };
  // cap=1000, total=500 → remaining=0.5 ちょうど → medium のしきい値(0.2)より上、
  // large のしきい値(0.5)も満たす
  const usage = makeUsage(1000, 500);
  const out = profilesMod.decide(profiles, usage, 1000000);
  assert.strictEqual(out.flow.tier, 'large');
});

test('decide: 上限が無い（cap<=0）なら no_cap_tier', () => {
  const profiles = { enabled: true, tiers: makeTiers(), policy: makePolicy(), state: {} };
  const usage = makeUsage(0, 999999999);
  const out = profilesMod.decide(profiles, usage, 1000000);
  assert.strictEqual(out.flow.tier, 'large');
  assert.match(out.flow.reason, /remaining=1\.00/);
});

test('decide: steps が該当なし・no_cap_tier 未宣言なら何も決めない', () => {
  const profiles = {
    enabled: true, tiers: makeTiers(),
    policy: makePolicy({ steps: [{ min_remaining_ratio: 0.9, tier: 'large' }], no_cap_tier: '' }),
    state: {},
  };
  const usage = makeUsage(1000, 999); // remaining ≈ 0.001 < 0.9 → 該当ステップ無し
  const out = profilesMod.decide(profiles, usage, 1000000);
  assert.deepStrictEqual(out, {});
});

test('decide: apply_to に無いワークロードは対象外', () => {
  const profiles = { enabled: true, tiers: makeTiers(), policy: makePolicy({ apply_to: ['project'] }), state: {} };
  // flow はほぼ枯渇（small 相当のはずの予算データ）だが apply_to に無いので出力に現れない。
  // project は使用実績が無く（usage.workloads に無い）cap<=0 相当 → no_cap_tier が選ばれる。
  const usage = makeUsage(1000, 999);
  const out = profilesMod.decide(profiles, usage, 1000000);
  assert.deepStrictEqual(Object.keys(out), ['project']);
  assert.strictEqual(out.project.tier, 'large');
});

test('decide: enabled=false なら何も決めない', () => {
  const profiles = { enabled: false, tiers: makeTiers(), policy: makePolicy(), state: {} };
  const usage = makeUsage(1000, 100);
  assert.deepStrictEqual(profilesMod.decide(profiles, usage, 1000000), {});
});

// --- decide(): ヒステリシス・最小保持 ---------------------------------------

test('decide: 下降は素直に効く（ヒステリシス無し）', () => {
  const profiles = {
    enabled: true, tiers: makeTiers(), policy: makePolicy(),
    state: { flow: { tier: 'large', candidate: { agent_cli: 'claude', model: 'opus' }, since: '2020-01-01T00:00:00Z', reason: '' } },
  };
  // remaining=0.3 → medium 相当。すぐ下がってよい
  const usage = makeUsage(1000, 700);
  const out = profilesMod.decide(profiles, usage, Date.parse('2020-01-01T00:20:00Z'));
  assert.strictEqual(out.flow.tier, 'medium');
});

test('decide: 上位への復帰はしきい値+ヒステリシスを満たすまで見送る', () => {
  const profiles = {
    enabled: true, tiers: makeTiers(), policy: makePolicy({ hysteresis: 0.05, min_hold_sec: 0 }),
    state: { flow: { tier: 'medium', candidate: { agent_cli: 'claude', model: 'sonnet' }, since: '2020-01-01T00:00:00Z', reason: '' } },
  };
  // remaining=0.52（large のしきい値 0.5 は超えるが 0.5+0.05=0.55 には届かない）→ 昇格を見送り medium 維持
  const notEnough = profilesMod.decide(profiles, makeUsage(1000, 480), Date.parse('2020-01-01T02:00:00Z'));
  assert.strictEqual(notEnough.flow.tier, 'medium');
  // remaining=0.56（0.55 を超える）→ 昇格
  const enough = profilesMod.decide(profiles, makeUsage(1000, 440), Date.parse('2020-01-01T02:00:00Z'));
  assert.strictEqual(enough.flow.tier, 'large');
});

test('decide: min_hold_sec 内は段が動かない。経過後は動く', () => {
  const policy = makePolicy({ min_hold_sec: 3600, hysteresis: 0 });
  const state = { flow: { tier: 'large', candidate: { agent_cli: 'claude', model: 'opus' }, since: '2020-01-01T00:00:00Z', reason: '' } };
  const usage = makeUsage(1000, 900); // remaining=0.1 → small 相当
  const within = profilesMod.decide(
    { enabled: true, tiers: makeTiers(), policy, state },
    usage, Date.parse('2020-01-01T00:30:00Z') // 30分後（保持1時間の内側）
  );
  assert.strictEqual(within.flow.tier, 'large', '保持時間内は前回の段を維持する');
  const after = profilesMod.decide(
    { enabled: true, tiers: makeTiers(), policy, state },
    usage, Date.parse('2020-01-01T01:30:00Z') // 90分後（保持1時間を超過）
  );
  assert.strictEqual(after.flow.tier, 'small', '保持時間経過後は動く');
});

// --- decide(): 候補（quota）フォールバック -----------------------------------

test('decide: 候補の CLI 枠が枯れていれば次点を採る', () => {
  const tiers = {
    large: { order: 2, label: '大', candidates: [{ agent_cli: 'claude', model: 'opus' }, { agent_cli: 'copilot', model: 'gpt-5' }] },
    small: { order: 1, label: '小', candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] },
  };
  const policy = makePolicy({ steps: [{ min_remaining_ratio: 0.0, tier: 'large' }], no_cap_tier: 'large' });
  const usage = {
    workloads: { flow: { tokenCap: 1000, totalTokens: 100 } },
    agents: { claude: { totalTokens: 5000000 } }, // 枯渇
    config: { allocation: { agents: { claude: { max_tokens: 3000000 } } } },
  };
  const out = profilesMod.decide({ enabled: true, tiers, policy, state: {} }, usage, 1000000);
  assert.strictEqual(out.flow.tier, 'large', 'tier 自体は budget 由来のまま（quota はここに影響しない）');
  assert.strictEqual(out.flow.candidate.agent_cli, 'copilot', '同じ段の次の候補へ');
});

test('decide: 段の候補が全滅なら一段下へ降りる', () => {
  const tiers = {
    large: { order: 2, label: '大', candidates: [{ agent_cli: 'claude', model: 'opus' }] },
    small: { order: 1, label: '小', candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] },
  };
  const policy = makePolicy({ steps: [{ min_remaining_ratio: 0.0, tier: 'large' }], no_cap_tier: 'large' });
  const usage = {
    workloads: { flow: { tokenCap: 1000, totalTokens: 100 } },
    agents: { claude: { totalTokens: 5000000 } },
    config: { allocation: { agents: { claude: { max_tokens: 3000000 } } } },
  };
  const out = profilesMod.decide({ enabled: true, tiers, policy, state: {} }, usage, 1000000);
  assert.strictEqual(out.flow.candidate.agent_cli, 'ollama');
  assert.match(out.flow.reason, /quota-fallback/);
});

test('decide: 最下位まで降りても全滅ならそのワークロードは書かない', () => {
  const tiers = {
    large: { order: 2, label: '大', candidates: [{ agent_cli: 'claude', model: 'opus' }] },
    small: { order: 1, label: '小', candidates: [{ agent_cli: 'claude', model: 'haiku' }] },
  };
  const policy = makePolicy({ steps: [{ min_remaining_ratio: 0.0, tier: 'large' }], no_cap_tier: 'large' });
  const usage = {
    workloads: { flow: { tokenCap: 1000, totalTokens: 100 } },
    agents: { claude: { totalTokens: 5000000 } },
    config: { allocation: { agents: { claude: { max_tokens: 3000000 } } } },
  };
  const out = profilesMod.decide({ enabled: true, tiers, policy, state: {} }, usage, 1000000);
  assert.strictEqual(out.flow.candidate, null);
  assert.match(out.flow.reason, /枯渇/);
});

// --- load/save: 正規化とフィールド単位マージ --------------------------------

test('load: profiles.json 不在・破損は既定値へフェイルセーフ（例外を投げない）', () => {
  const dir = tmpdir('orch-profiles-missing-');
  const loaded = profilesMod.loadProfiles(dir);
  assert.strictEqual(loaded.exists, false);
  assert.strictEqual(loaded.enabled, true);
  assert.deepStrictEqual(loaded.tiers, {});
  fs.writeFileSync(path.join(dir, 'profiles.json'), 'not-json{{{');
  const loaded2 = profilesMod.loadProfiles(dir);
  assert.strictEqual(loaded2.exists, false);
});

test('save: 不正な候補・不正な段は黙って落とす', () => {
  const dir = tmpdir('orch-profiles-save-');
  const saved = profilesMod.save(cfgFor(dir), {
    tiers: {
      large: { order: 3, candidates: [{ agent_cli: 'claude', model: 'opus' }, 'not-an-object', {}] },
      broken: 'not-an-object',
      noOrder: { candidates: [{ agent_cli: 'x' }] },
    },
  });
  assert.deepStrictEqual(Object.keys(saved.tiers), ['large']);
  assert.strictEqual(saved.tiers.large.candidates.length, 1);
});

test('save: policy はフィールド単位でマージする（未送信のフィールドは保持）', () => {
  const dir = tmpdir('orch-profiles-merge-');
  profilesMod.save(cfgFor(dir), { policy: { apply_to: ['flow'], interval_sec: 120 } });
  // 別の保存で apply_to だけ更新 → interval_sec は前回値のまま残る
  const saved = profilesMod.save(cfgFor(dir), { policy: { apply_to: ['flow', 'project'] } });
  assert.deepStrictEqual(saved.policy.apply_to, ['flow', 'project']);
  assert.strictEqual(saved.policy.interval_sec, 120, 'interval_sec は保存フォームが送らなくても消えない');
});

test('save: state は触らない（apply() 専有）', () => {
  const dir = tmpdir('orch-profiles-state-');
  const controlDir = tmpdir('orch-control-for-state-');
  const cfg = cfgFor(controlDir);
  fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
    version: 1, enabled: true,
    tiers: { small: { order: 1, candidates: [{ agent_cli: 'ollama' }] } },
    policy: { apply_to: [] },
    state: { flow: { tier: 'small', candidate: { agent_cli: 'ollama' }, since: '2020-01-01T00:00:00Z', reason: 'x' } },
  }));
  const saved = profilesMod.save(cfg, { enabled: false });
  assert.deepStrictEqual(saved.state, { flow: { tier: 'small', candidate: { agent_cli: 'ollama' }, since: '2020-01-01T00:00:00Z', reason: 'x' } });
  void dir;
});

// --- apply(): 副作用（control.json / profiles.json への書き込み） -----------

function seedBudget(budgetDir, cap, totalSeconds, tokensIn) {
  fs.mkdirSync(path.join(budgetDir, 'ledger'), { recursive: true });
  const day = (() => {
    const d = new Date();
    return String(d.getUTCFullYear()) + String(d.getUTCMonth() + 1).padStart(2, '0') + String(d.getUTCDate()).padStart(2, '0');
  })();
  fs.writeFileSync(
    path.join(budgetDir, 'config.json'),
    JSON.stringify({ version: 2, tokens: 0, period: 'day', allocation: { workloads: { flow: { max_tokens: cap } } } })
  );
  fs.writeFileSync(
    path.join(budgetDir, 'ledger', `${day}.jsonl`),
    JSON.stringify({ ts: 'x', workload: 'flow', seconds: totalSeconds, agent_cli: 'claude', model: 'opus', tokens_in: tokensIn, tokens_out: 0 }) + '\n'
  );
}

test('apply: 決定を control.json と profiles.json の state へ書く', () => {
  const controlDir = tmpdir('orch-apply-control-');
  const budgetDir = tmpdir('orch-apply-budget-');
  const cfg = cfgFor(controlDir, budgetDir);
  seedBudget(budgetDir, 1000, 1, 100); // remaining = 0.9 → large
  fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
    version: 1, enabled: true,
    tiers: { large: { order: 2, candidates: [{ agent_cli: 'claude', model: 'opus' }] },
             small: { order: 1, candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] } },
    policy: { apply_to: ['flow'], steps: [{ min_remaining_ratio: 0.5, tier: 'large' }, { min_remaining_ratio: 0, tier: 'small' }],
              no_cap_tier: 'large', hysteresis: 0, min_hold_sec: 0 },
    state: {},
  }));
  const result = profilesMod.apply(cfg);
  assert.strictEqual(result.controlWritten, true);
  assert.strictEqual(result.stateWritten, true);
  const ctrl = control.loadControl(controlDir);
  assert.strictEqual(ctrl.workloads.flow.agent_cli, 'claude');
  assert.strictEqual(ctrl.workloads.flow.model, 'opus');
  assert.strictEqual(ctrl.revision, 1);
  const profilesAfter = profilesMod.loadProfiles(controlDir);
  assert.strictEqual(profilesAfter.state.flow.tier, 'large');
});

test('apply: 決定が現状と同じなら control.json を書かない（revision が増えない）', () => {
  const controlDir = tmpdir('orch-apply-idempotent-');
  const budgetDir = tmpdir('orch-apply-idempotent-budget-');
  const cfg = cfgFor(controlDir, budgetDir);
  seedBudget(budgetDir, 1000, 1, 100); // remaining = 0.9 → large（毎回同じ）
  fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
    version: 1, enabled: true,
    tiers: { large: { order: 2, candidates: [{ agent_cli: 'claude', model: 'opus' }] },
             small: { order: 1, candidates: [{ agent_cli: 'ollama', model: 'qwen3' }] } },
    policy: { apply_to: ['flow'], steps: [{ min_remaining_ratio: 0.5, tier: 'large' }, { min_remaining_ratio: 0, tier: 'small' }],
              no_cap_tier: 'large', hysteresis: 0, min_hold_sec: 0 },
    state: {},
  }));
  const first = profilesMod.apply(cfg);
  assert.strictEqual(first.controlWritten, true);
  const revisionAfterFirst = control.loadControl(controlDir).revision;

  const second = profilesMod.apply(cfg);
  assert.strictEqual(second.controlWritten, false, '同じ候補なら control.json を書かない');
  assert.strictEqual(second.stateWritten, false, '同じ決定なら state も書かない（reason も変化なし）');
  assert.strictEqual(control.loadControl(controlDir).revision, revisionAfterFirst, 'revision は増えない');
});

test('apply: 候補が枯渇して書けないワークロードは control.json に触れない', () => {
  const controlDir = tmpdir('orch-apply-exhausted-');
  const budgetDir = tmpdir('orch-apply-exhausted-budget-');
  const cfg = cfgFor(controlDir, budgetDir);
  seedBudget(budgetDir, 1000, 1, 100);
  fs.writeFileSync(path.join(controlDir, 'profiles.json'), JSON.stringify({
    version: 1, enabled: true,
    tiers: { large: { order: 1, candidates: [{ agent_cli: 'claude', model: 'opus' }] } },
    policy: { apply_to: ['flow'], steps: [{ min_remaining_ratio: 0.0, tier: 'large' }], no_cap_tier: 'large', hysteresis: 0, min_hold_sec: 0 },
    state: {},
  }));
  fs.writeFileSync(path.join(budgetDir, 'config.json'), JSON.stringify({
    version: 2, tokens: 0, period: 'day',
    allocation: { workloads: { flow: { max_tokens: 1000 } }, agents: { claude: { max_tokens: 1 } } },
  }));
  const result = profilesMod.apply(cfg);
  assert.strictEqual(result.controlWritten, false);
  const ctrl = control.loadControl(controlDir);
  assert.deepStrictEqual(ctrl.workloads, {});
});

console.log(`\n${passed} passed`);
