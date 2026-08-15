'use strict';

// Orchestration feature のテスト（Electron 不使用）。
// - ノード予算 v2: トークン集計（実測/推定の内訳）・配分（rebalance のクランプ）・レート較正（中央値）
// - エージェント制御（agent-control）: saveControl の revision 単調増加・setLifecycle・status/ の fresh 判定
// - エージェント CLI ドロップイン（agent-cli）: first-wins の陰り・契約検証・組み込み名の拒否

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const budget = require('../src/features/orchestration/main/budget');
const { engineConfig } = require('./helpers/engine-status');
const control = require('../src/features/orchestration/main/control');
const agents = require('../src/features/orchestration/main/agents');
const instructions = require('../src/features/orchestration/main/instructions');
const tuning = require('../src/features/orchestration/main/tuning');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function utcDay() {
  const d = new Date();
  return (
    String(d.getUTCFullYear()) +
    String(d.getUTCMonth() + 1).padStart(2, '0') +
    String(d.getUTCDate()).padStart(2, '0')
  );
}

function writeLedger(dir, day, records) {
  fs.mkdirSync(path.join(dir, 'ledger'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'ledger', `${day}.jsonl`),
    records.map((r) => (typeof r === 'string' ? r : JSON.stringify(r))).join('\n') + '\n'
  );
}

function budgetCfg(dir) {
  return { orchestration: { budgetDir: dir, controlDir: '' } };
}

function controlCfg(dir) {
  return { orchestration: { budgetDir: '', controlDir: dir } };
}

function instrCfg(dir) {
  return { orchestration: { instructionsDir: dir } };
}

function tuningCfg(tuningDir, methodsDir) {
  return { orchestration: { tuningDir, methodsDir } };
}

// --- ノード予算 v2: トークン集計（実測 + 推定） ------------------------------

test('予算 v2: 実測トークンと推定トークン（seconds × rate）を分けて集計する', () => {
  const dir = tmpdir('orch-budget-');
  // 設定: トークン上限 100 万、レート表（ollama:qwen3 = 40 tok/s、既定 120 tok/s）
  fs.writeFileSync(
    path.join(dir, 'config.json'),
    JSON.stringify({
      version: 2,
      tokens: 1000000,
      period: 'day',
      rates: { default_tokens_per_second: 120, per_cli: { 'ollama:qwen3': 40 } },
    })
  );
  writeLedger(dir, utcDay(), [
    // 実測行（tokens_in/out あり）— workload=project
    { ts: 'x', workload: 'project', seconds: 5, agent_cli: 'claude', model: 'opus', tokens_in: 12000, tokens_out: 3400 },
    // 推定行（tokens 無し・rate=cli:model=40）— seconds 100 × 40 = 4000 — workload=flow
    { ts: 'x', workload: 'flow', seconds: 100, agent_cli: 'ollama', model: 'qwen3' },
    // 推定行（rate=default=120）— seconds 10 × 120 = 1200 — workload=routine
    { ts: 'x', workload: 'routine', seconds: 10, agent_cli: 'kiro' },
    'broken-not-json',
  ]);
  const u = budget.usage(budgetCfg(dir));
  // 実測: project 15400
  assert.strictEqual(u.workloads.project.measuredTokens, 15400);
  assert.strictEqual(u.workloads.project.estimatedTokens, 0);
  // 推定: flow 4000 / routine 1200
  assert.strictEqual(u.workloads.flow.estimatedTokens, 4000);
  assert.strictEqual(u.workloads.flow.measuredTokens, 0);
  assert.strictEqual(u.workloads.routine.estimatedTokens, 1200);
  // 合計の内訳
  assert.strictEqual(u.totalTokens.measured, 15400);
  assert.strictEqual(u.totalTokens.estimated, 5200);
  assert.strictEqual(u.totalTokens.total, 20600);
  // v1 互換: 秒集計も残る
  assert.strictEqual(u.totalSeconds, 115);
  assert.strictEqual(u.totals.flow, 100);
  assert.strictEqual(u.totalRecords, 3);
  assert.strictEqual(u.workloads.project.recordCount, 1);
  assert.strictEqual(u.workloads.flow.recordCount, 1);
  assert.strictEqual(u.totalUnestimatedRecords, 0);
  assert.strictEqual(u.agents.claude.measuredTokens, 15400);
  assert.strictEqual(u.agents.ollama.estimatedTokens, 4000);
  assert.strictEqual(u.agents.kiro.recordCount, 1);
  assert.strictEqual(u.tokenLimit, 1000000);
  assert.strictEqual(u.exceeded, false);
});

test('予算 v2: 推定レートがない実行を未使用ではなく推定不能として数える', () => {
  const dir = tmpdir('orch-budget-');
  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify({ version: 2, period: 'day' }));
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'flow', seconds: 30, agent_cli: 'unknown' },
    { ts: 'x', workload: 'flow', seconds: 15, agent_cli: 'unknown' },
  ]);

  const u = budget.usage(budgetCfg(dir));
  assert.strictEqual(u.totalTokens.total, 0);
  assert.strictEqual(u.totalSeconds, 45);
  assert.strictEqual(u.totalRecords, 2);
  assert.strictEqual(u.totalUnestimatedRecords, 2);
  assert.strictEqual(u.workloads.flow.recordCount, 2);
  assert.strictEqual(u.workloads.flow.unestimatedRecords, 2);
  assert.strictEqual(u.workloads.project.recordCount, 0);
  assert.strictEqual(u.agents.unknown.recordCount, 2);
  assert.strictEqual(u.agents.unknown.seconds, 45);
  assert.strictEqual(u.agents.unknown.unestimatedRecords, 2);
});

test('予算 v2: トークン上限（全体）超過と実効上限（per-workload）soft/exceeded 判定', () => {
  const dir = tmpdir('orch-budget-');
  fs.writeFileSync(
    path.join(dir, 'config.json'),
    JSON.stringify({
      version: 2,
      tokens: 10000,
      period: 'day',
      allocation: {
        soft_ratio: 0.9,
        workloads: { project: { max_tokens: 5000 } },
      },
    })
  );
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'project', seconds: 1, tokens_in: 4600, tokens_out: 0 }, // 4600 >= 0.9*5000=4500 → soft
    { ts: 'x', workload: 'flow', seconds: 1, tokens_in: 6000, tokens_out: 0 },
  ]);
  const u = budget.usage(budgetCfg(dir));
  // per-workload 実効上限 = allocation.max_tokens（computed 無し）
  assert.strictEqual(u.workloads.project.tokenCap, 5000);
  assert.strictEqual(u.workloads.project.soft, true);
  assert.strictEqual(u.workloads.project.tokenExceeded, false); // 4600 < 5000
  // 全体 10600 >= 10000 → 超過
  assert.strictEqual(u.totalTokens.total, 10600);
  assert.strictEqual(u.tokenExceededTotal, true);
  assert.strictEqual(u.exceeded, true);
});

test('予算 v2: save は allocation を検証して version:2 で原子書換する', () => {
  const dir = tmpdir('orch-budget-');
  budget.save(budgetCfg(dir), {
    tokens: 2000000,
    allocation: {
      mode: 'auto',
      soft_ratio: 0.8,
      workloads: { routine: { weight: 1, min_tokens: 100000, on_exhausted: 'stop' } },
      agents: { claude: { max_tokens: '500000' } },
    },
  });
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  assert.strictEqual(raw.version, 2);
  assert.strictEqual(raw.tokens, 2000000);
  assert.strictEqual(raw.allocation.mode, 'auto');
  assert.strictEqual(raw.allocation.soft_ratio, 0.8);
  assert.strictEqual(raw.allocation.workloads.routine.on_exhausted, 'stop');
  assert.strictEqual(raw.allocation.agents.claude.max_tokens, 500000);
  assert.strictEqual(raw.updated_by, 'dashboard');
  // 検証: 負値・不正 enum は弾く
  assert.throws(() => budget.save(budgetCfg(dir), { tokens: -1 }));
  assert.throws(() => budget.save(budgetCfg(dir), { allocation: { mode: 'weird' } }));
  assert.throws(() =>
    budget.save(budgetCfg(dir), { allocation: { workloads: { routine: { on_exhausted: 'kill' } } } })
  );
  assert.throws(() =>
    budget.save(budgetCfg(dir), { allocation: { agents: { claude: { max_tokens: -1 } } } })
  );
  // 部分更新: 前回の allocation が保持される
  budget.save(budgetCfg(dir), { period: 'month' });
  const raw2 = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  assert.strictEqual(raw2.period, 'month');
  assert.strictEqual(raw2.allocation.workloads.routine.min_tokens, 100000);
  assert.strictEqual(raw2.allocation.agents.claude.max_tokens, 500000);
});

// --- ノード予算 v2: 配分（rebalance のクランプ） ------------------------------

test('予算 v2: rebalance は R を weight 比で配り min/max でクランプする', () => {
  const dir = tmpdir('orch-rebal-');
  fs.writeFileSync(
    path.join(dir, 'config.json'),
    JSON.stringify({
      version: 2,
      tokens: 1000000,
      period: 'total',
      computed: { note: '別の書き手が置いた未知キー' },
      allocation: {
        mode: 'auto',
        workloads: {
          routine: { weight: 1, min_tokens: 500000 }, // 下限クランプ（引き上げ）
          project: { weight: 3, max_tokens: 400000 }, // 上限クランプ（引き下げ）
          flow: { weight: 1 }, // クランプ無し
          amigos: { weight: 0 }, // weight 0 = 非アクティブ
        },
      },
    })
  );
  // 消費: routine 10 万・project 20 万（実測）→ 合計 30 万・R = 70 万
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'routine', seconds: 1, tokens_in: 100000, tokens_out: 0 },
    { ts: 'x', workload: 'project', seconds: 1, tokens_in: 200000, tokens_out: 0 },
  ]);
  budget.rebalance(budgetCfg(dir));
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  const c = raw.computed.workloads;
  // active weight 合計 = 1+3+1 = 5
  // routine: 100000 + 700000*1/5=140000 = 240000 → min 500000 で引き上げ
  assert.strictEqual(c.routine.tokens, 500000);
  // project: 200000 + 700000*3/5=420000 = 620000 → max 400000 で引き下げ
  assert.strictEqual(c.project.tokens, 400000);
  // flow: 0 + 700000*1/5=140000 = 140000（クランプ無し）
  assert.strictEqual(c.flow.tokens, 140000);
  // amigos: weight 0 = computed に書かれない
  assert.strictEqual(c.amigos, undefined);
  assert.strictEqual(raw.computed.computed_by, 'dashboard');
  assert.ok(raw.computed.computed_at);
  assert.strictEqual(raw.computed.note, '別の書き手が置いた未知キー', 'computed の未知キーを消さない');
});

test('予算 v2: 全体上限0の rebalance は無制限を維持する', () => {
  const dir = tmpdir('orch-rebal-unlimited-');
  fs.writeFileSync(
    path.join(dir, 'config.json'),
    JSON.stringify({
      version: 2,
      tokens: 0,
      period: 'total',
      allocation: {
        mode: 'auto',
        workloads: {
          routine: { weight: 1 },
          flow: { weight: 1 },
        },
      },
    })
  );
  writeLedger(dir, utcDay(), [
    { ts: 'x', workload: 'routine', seconds: 1, tokens_in: 519402, tokens_out: 0 },
  ]);

  const result = budget.rebalance(budgetCfg(dir));
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));

  assert.deepStrictEqual(raw.computed.workloads, {});
  assert.strictEqual(result.workloads.routine.tokenCap, 0);
  assert.strictEqual(result.workloads.routine.exceeded, false);
  assert.strictEqual(result.exceeded, false);
});

// --- ノード予算 v2: レート較正（中央値） -------------------------------------

test('予算 v2: calibrateRates は seconds と実測が両方ある行から中央値レートを書く', () => {
  const dir = tmpdir('orch-calib-');
  writeLedger(dir, utcDay(), [
    // claude:opus — 180 / 200 / 160 → 中央値 180
    { ts: 'x', workload: 'project', seconds: 10, agent_cli: 'claude', model: 'opus', tokens_in: 1000, tokens_out: 800 },
    { ts: 'x', workload: 'project', seconds: 10, agent_cli: 'claude', model: 'opus', tokens_in: 1000, tokens_out: 1000 },
    { ts: 'x', workload: 'project', seconds: 10, agent_cli: 'claude', model: 'opus', tokens_in: 1000, tokens_out: 600 },
    // kiro（モデル無し）— 1000/10 = 100
    { ts: 'x', workload: 'routine', seconds: 10, agent_cli: 'kiro', tokens_in: 500, tokens_out: 500 },
    // seconds はあるがトークン無し → 無視
    { ts: 'x', workload: 'flow', seconds: 10, agent_cli: 'kiro' },
    // トークンはあるが seconds 0 → 無視
    { ts: 'x', workload: 'flow', seconds: 0, agent_cli: 'kiro', tokens_in: 999, tokens_out: 0 },
  ]);
  const rates = budget.calibrateRates(budgetCfg(dir));
  assert.strictEqual(rates.per_cli['claude:opus'], 180);
  assert.strictEqual(rates.per_cli.kiro, 100);
  // 書き戻しも確認
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'config.json'), 'utf8'));
  assert.strictEqual(raw.rates.per_cli['claude:opus'], 180);
  assert.strictEqual(raw.version, 2);
});

// --- ノード予算 v2: quota 観測の導出 -----------------------------------------
// 観測は台帳の行として届き、状態ファイルにはしない。書き手を増やさず、古い観測の掃除も
// 要らない（period 窓から自然に落ちる）ことがこの設計の要点。

test('予算 v2: quota 観測行は消費に数えず、CLI ごとの現在状態へ畳む', () => {
  const dir = tmpdir('orch-quota-');
  writeLedger(dir, utcDay(), [
    { ts: '2020-01-01T00:00:00Z', workload: 'flow', seconds: 5, agent_cli: 'claude', tokens_in: 100, tokens_out: 0 },
    { ts: '2020-01-01T00:01:00Z', workload: 'flow', seconds: 0, agent_cli: 'claude', quota_kind: 'rate_limit', reset_at: '2020-01-01T01:00:00Z' },
    { ts: '2020-01-01T00:02:00Z', workload: 'flow', seconds: 0, agent_cli: 'codex', quota_kind: 'exhausted' },
  ]);
  const u = budget.usage(budgetCfg(dir));
  assert.strictEqual(u.workloads.flow.totalTokens, 100, '観測行はトークンを増やさない');
  assert.strictEqual(u.workloads.flow.seconds, 5, '観測行は秒を増やさない');
  assert.strictEqual(u.workloads.flow.recordCount, 1, '観測行は実行回数に数えない');
  assert.deepStrictEqual(u.config.computed.agents.claude,
    { quota_kind: 'rate_limit', observed_at: '2020-01-01T00:01:00Z', reset_at: '2020-01-01T01:00:00Z' });
  assert.strictEqual(u.config.computed.agents.codex.quota_kind, 'exhausted');
});

test('予算 v2: 同じ CLI の観測は最後の 1 件が現在の状態', () => {
  const dir = tmpdir('orch-quota-latest-');
  writeLedger(dir, utcDay(), [
    { ts: '2020-01-01T00:00:00Z', workload: 'flow', seconds: 0, agent_cli: 'claude', quota_kind: 'exhausted' },
    { ts: '2020-01-01T00:05:00Z', workload: 'flow', seconds: 0, agent_cli: 'claude', quota_kind: 'rate_limit', reset_at: '2020-01-01T01:00:00Z' },
  ]);
  const agentsState = budget.usage(budgetCfg(dir)).config.computed.agents;
  assert.strictEqual(agentsState.claude.quota_kind, 'rate_limit');
});

test('予算 v2: CLI quota snapshot は使用率を保持し、回復snapshotで制限を解除する', () => {
  const derived = budget.quotaAgentsFrom([
    { ts: '2026-08-12T00:00:00Z', event: 'quota_snapshot', agent_cli: 'codex',
      quota_kind: 'rate_limit', quota_used_percent: 100, reset_at: '2026-08-17T00:00:00Z' },
    { ts: '2026-08-12T00:05:00Z', event: 'quota_snapshot', agent_cli: 'codex',
      quota_used_percent: 33, quota_source: 'codex-app-server', reset_at: '2026-08-17T00:00:00Z' },
  ]);
  assert.strictEqual(derived.codex.quota_kind, undefined);
  assert.strictEqual(derived.codex.quota_used_percent, 33);
  assert.strictEqual(derived.codex.quota_source, 'codex-app-server');
});

test('予算 v2: 復帰時刻の無い rate_limit は観測時刻 + 既定 TTL で失効する', () => {
  // CLI がメッセージに復帰時刻を載せないのは普通にある。塞ぎっぱなしにすると
  // 「止めないための機能」が「二度と使えない」になる。
  const derived = budget.quotaAgentsFrom([
    { ts: '2020-01-01T00:00:00Z', agent_cli: 'claude', quota_kind: 'rate_limit' },
  ]);
  assert.strictEqual(
    derived.claude.reset_at,
    new Date(Date.parse('2020-01-01T00:00:00Z') + budget.QUOTA_RATE_LIMIT_TTL_SEC * 1000).toISOString()
  );
});

test('予算 v2: 観測時刻すら読めない行は誰も塞がない', () => {
  const derived = budget.quotaAgentsFrom([
    { ts: 'not-a-date', agent_cli: 'claude', quota_kind: 'rate_limit' },
    { agent_cli: '', quota_kind: 'exhausted' },
  ]);
  assert.ok(Date.parse(derived.claude.reset_at) <= 0, '過去の時刻 = すぐ復帰扱い');
  assert.strictEqual(Object.keys(derived).length, 1, 'agent_cli の無い行は落とす');
});

test('予算 v2: rate 解決は cli:model → cli → default → 0 の順', () => {
  const cfg = { rates: { default_tokens_per_second: 120, per_cli: { 'claude:opus': 180, claude: 150 } } };
  assert.strictEqual(budget.rate(cfg, 'claude', 'opus'), 180);
  assert.strictEqual(budget.rate(cfg, 'claude', 'haiku'), 150); // model 未登録 → cli
  assert.strictEqual(budget.rate(cfg, 'ollama', 'x'), 120); // 未登録 → default
  assert.strictEqual(budget.rate({}, 'ollama'), 0); // レート表なし → 0
});

// --- エージェント制御（agent-control） --------------------------------------

test('制御: loadControl は無ければ既定を返す', () => {
  const dir = tmpdir('orch-ctrl-');
  const c = control.loadControl(dir);
  assert.deepStrictEqual(c, { version: 1, revision: 0, defaults: {}, workloads: {} });
});

test('制御: saveControl は patch をマージし revision を単調増加させる', () => {
  const dir = tmpdir('orch-ctrl-');
  const c1 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { planner: { model: 'opus' } }, delegation: { prefer: 'remote', max_open_issues: 8 } } },
  });
  assert.strictEqual(c1.revision, 1);
  assert.strictEqual(c1.workloads.flow.agents.planner.model, 'opus');
  assert.strictEqual(c1.workloads.flow.delegation.prefer, 'remote');
  const c2 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { worker: { agent_cli: 'cursor' } } } },
  });
  assert.strictEqual(c2.revision, 2);
  // 既存の planner 上書きは保持され worker が追加される（深いマージ）
  assert.strictEqual(c2.workloads.flow.agents.planner.model, 'opus');
  assert.strictEqual(c2.workloads.flow.agents.worker.agent_cli, 'cursor');
  assert.strictEqual(c2.updated_by, 'dashboard');
  // 検証: 不正 lifecycle / prefer は弾く
  assert.throws(() => control.saveControl(controlCfg(dir), { workloads: { flow: { lifecycle: 'kill' } } }));
  assert.throws(() =>
    control.saveControl(controlCfg(dir), { workloads: { flow: { delegation: { prefer: 'sideways' } } } })
  );
});

test('制御: agents.<key> は null 指定でキー削除できる（UI の「削除」動線）', () => {
  const dir = tmpdir('orch-ctrl-');
  control.saveControl(controlCfg(dir), {
    workloads: { project: { agents: { plan: { model: 'opus' }, verify: { agent_cli: 'kiro' } } } },
  });
  // UI が「削除」チェックで送る null。plan を消し verify は残す。
  const c = control.saveControl(controlCfg(dir), {
    workloads: { project: { agents: { plan: null } } },
  });
  assert.strictEqual(c.workloads.project.agents.plan, undefined);
  assert.strictEqual(c.workloads.project.agents.verify.agent_cli, 'kiro');
  assert.strictEqual(c.revision, 2);
});

test('制御: flow の共通 timeout は正数秒を保存し、空指定で継承へ戻す', () => {
  const dir = tmpdir('orch-ctrl-');
  const c1 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { timeout_sec: 900 } },
  });
  assert.strictEqual(c1.workloads.flow.timeout_sec, 900);
  const c2 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { timeout_sec: null } },
  });
  assert.strictEqual(c2.workloads.flow.timeout_sec, undefined);
  for (const bad of [0, -1, 1.5, 'x']) {
    assert.throws(() => control.saveControl(controlCfg(dir), {
      workloads: { flow: { timeout_sec: bad } },
    }));
  }
});

test('制御: flow の用途別 timeout は他の上書きを保ったまま更新・削除できる', () => {
  const dir = tmpdir('orch-ctrl-');
  control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { verify: { model: 'qwen3', timeout_sec: 300 } } } },
  });
  const c1 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { verify: { timeout_sec: 600 } } } },
  });
  assert.deepStrictEqual(c1.workloads.flow.agents.verify, { model: 'qwen3', timeout_sec: 600 });
  const c2 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { verify: { timeout_sec: null } } } },
  });
  assert.deepStrictEqual(c2.workloads.flow.agents.verify, { model: 'qwen3' });
  assert.throws(() => control.saveControl(controlCfg(dir), {
    workloads: { flow: { agents: { verify: { timeout_sec: 0 } } } },
  }));
});

test('制御: concurrency は flow の同時実行数を宣言し、空指定でキーを消す', () => {
  const dir = tmpdir('orch-ctrl-');
  const c1 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { concurrency: { max_runs: 2, workers: 1 } } },
  });
  assert.strictEqual(c1.workloads.flow.concurrency.max_runs, 2);
  assert.strictEqual(c1.workloads.flow.concurrency.workers, 1);
  // max_runs の 0 は「上限なし」で有効な宣言（既定へ戻すのは空指定）
  const c2 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { concurrency: { max_runs: 0 } } },
  });
  assert.strictEqual(c2.workloads.flow.concurrency.max_runs, 0);
  assert.strictEqual(c2.workloads.flow.concurrency.workers, 1);
  // UI の空欄（null）はキー削除 → 各プロジェクトの設定へ戻る
  const c3 = control.saveControl(controlCfg(dir), {
    workloads: { flow: { concurrency: { max_runs: null, workers: null } } },
  });
  assert.strictEqual(c3.workloads.flow.concurrency, undefined);
  // 検証: 負数・小数・workers=0（ワーカー無し＝run が進まない）は弾く
  for (const bad of [{ max_runs: -1 }, { workers: 0 }, { workers: 1.5 }, { max_runs: 'x' }]) {
    assert.throws(() => control.saveControl(controlCfg(dir), { workloads: { flow: { concurrency: bad } } }));
  }
});

test('制御: setLifecycle は lifecycle を設定し revision を +1 する', () => {
  const dir = tmpdir('orch-ctrl-');
  const c1 = control.setLifecycle(controlCfg(dir), { workload: 'routine', action: 'pause' });
  assert.strictEqual(c1.revision, 1);
  assert.strictEqual(c1.workloads.routine.lifecycle, 'pause');
  const c2 = control.setLifecycle(controlCfg(dir), { workload: 'routine', action: 'run' });
  assert.strictEqual(c2.revision, 2);
  assert.strictEqual(c2.workloads.routine.lifecycle, 'run');
  assert.throws(() => control.setLifecycle(controlCfg(dir), { workload: 'routine', action: 'boom' }));
  assert.throws(() => control.setLifecycle(controlCfg(dir), { action: 'run' }));
});

test('制御: readStatus は status/*.json を読み fresh 判定を付ける', () => {
  const dir = tmpdir('orch-ctrl-');
  const statusDir = path.join(dir, 'status');
  fs.mkdirSync(statusDir, { recursive: true });
  const now = new Date();
  const fresh = new Date(now.getTime() - 30 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  const stale = new Date(now.getTime() - 600 * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  fs.writeFileSync(path.join(statusDir, 'kiro-loop-1.json'), JSON.stringify({
    tool: 'kiro-loop', workload: 'routine', pid: 1, revision_applied: 3,
    lifecycle: 'run', fresh_after_sec: 120, ts: fresh,
  }));
  fs.writeFileSync(path.join(statusDir, 'agent-flow-2.json'), JSON.stringify({
    tool: 'agent-flow', workload: 'flow', pid: 2, fresh_after_sec: 120, ts: stale,
  }));
  fs.writeFileSync(path.join(statusDir, 'broken.json'), '{not json');
  const rows = control.readStatus(dir);
  const byTool = Object.fromEntries(rows.map((r) => [r.tool, r]));
  assert.strictEqual(rows.length, 2); // 壊れた行は無視
  assert.strictEqual(byTool['kiro-loop'].fresh, true); // 30s 経過 → 猶予 360s 以内
  assert.strictEqual(byTool['agent-flow'].fresh, false); // 600s 経過 → stale
  // 欠損ディレクトリでも空配列
  assert.deepStrictEqual(control.readStatus(tmpdir('orch-empty-')), []);
});

// --- エージェント CLI ドロップイン（agent-cli） ------------------------------

test('エージェント: list は first-wins で同名を陰らせ、契約違反を errors に集める', () => {
  const savedEnv = process.env.KIRO_AGENTS_DIR;
  const dir1 = tmpdir('orch-agents-hi-'); // 最優先（KIRO_AGENTS_DIR）
  const root2 = tmpdir('orch-agents-root-');
  const dir2 = path.join(root2, 'agents'); // 次点（projects.roots/agents）
  fs.mkdirSync(dir2, { recursive: true });
  // 同名 cursor が両方に → dir1 が勝ち dir2 は陰る
  fs.writeFileSync(path.join(dir1, 'cursor.json'), JSON.stringify({ command: ['cursor', 'run'] }));
  fs.writeFileSync(path.join(dir2, 'cursor.json'), JSON.stringify({ command: ['old'] }));
  // 契約違反（command 空・output 不正・errors.class 不正）
  fs.writeFileSync(path.join(dir1, 'bad.json'), JSON.stringify({
    command: [], output: 'weird', errors: [{ match: 'x', class: 'nope' }], extra: 1,
  }));
  // 組み込み名の上書きは無視される旨を errors に
  fs.writeFileSync(path.join(dir1, 'kiro.json'), JSON.stringify({ command: ['kiro'] }));
  try {
    process.env.KIRO_AGENTS_DIR = dir1;
    const cfg = { ...engineConfig([root2]), orchestration: {} };
    const res = agents.list(cfg);
    assert.deepStrictEqual(res.builtins, ['kiro', 'claude', 'copilot', 'codex']);
    const byPath = Object.fromEntries(res.dropins.map((d) => [d.path, d]));
    const hiCursor = byPath[path.join(dir1, 'cursor.json')];
    const loCursor = byPath[path.join(dir2, 'cursor.json')];
    assert.strictEqual(hiCursor.shadowed, false);
    assert.strictEqual(loCursor.shadowed, true, '後段の同名は陰る');
    const bad = byPath[path.join(dir1, 'bad.json')];
    assert.ok(bad.errors.some((e) => e.includes('command')));
    assert.ok(bad.errors.some((e) => e.includes('output')));
    assert.ok(bad.errors.some((e) => e.includes('class')));
    assert.ok(bad.errors.some((e) => e.includes('未知のフィールド')));
    const builtin = byPath[path.join(dir1, 'kiro.json')];
    assert.ok(builtin.errors.some((e) => e.includes('組み込み名')));
  } finally {
    if (savedEnv === undefined) delete process.env.KIRO_AGENTS_DIR;
    else process.env.KIRO_AGENTS_DIR = savedEnv;
  }
});

test('エージェント: save は検証を通し組み込み名を拒否、remove は既知ディレクトリだけ', () => {
  const savedEnv = process.env.KIRO_AGENTS_DIR;
  const dir = tmpdir('orch-agents-save-');
  try {
    process.env.KIRO_AGENTS_DIR = dir;
    const cfg = { orchestration: {} };
    // 組み込み名は拒否
    assert.throws(() => agents.save(cfg, { name: 'kiro', spec: { command: ['x'] }, dir }), /組み込み名/);
    // 契約違反は拒否
    assert.throws(() => agents.save(cfg, { name: 'cursor', spec: { command: [] }, dir }), /契約/);
    // 妥当な定義は書ける
    const r = agents.save(cfg, { name: 'cursor', spec: { command: ['cursor', 'run', '{model}'] }, dir });
    assert.strictEqual(r.path, path.join(dir, 'cursor.json'));
    assert.ok(fs.existsSync(r.path));
    // remove は既知ディレクトリ（KIRO_AGENTS_DIR）配下なので通る
    agents.remove(cfg, { name: 'cursor', dir });
    assert.ok(!fs.existsSync(r.path));
    // 未知ディレクトリの削除は拒否
    assert.throws(() => agents.remove(cfg, { name: 'cursor', dir: tmpdir('orch-unknown-') }), /既知/);
  } finally {
    if (savedEnv === undefined) delete process.env.KIRO_AGENTS_DIR;
    else process.env.KIRO_AGENTS_DIR = savedEnv;
  }
});

// --- グローバル指示（agent-instructions） -----------------------------------

test('指示: loadInstructions は無ければ既定（revision:0・enabled:true・空）を返す', () => {
  const dir = tmpdir('orch-instr-');
  const gi = instructions.loadInstructions(dir);
  assert.strictEqual(gi.revision, 0);
  assert.strictEqual(gi.enabled, true);
  assert.strictEqual(gi.text, '');
  assert.deepStrictEqual(gi.skills, []);
  assert.strictEqual(gi.max_chars, 2000);
});

test('指示: saveInstructions は patch をマージし revision を単調増加させる', () => {
  const dir = tmpdir('orch-instr-');
  const c1 = instructions.saveInstructions(instrCfg(dir), {
    text: '回答は日本語。',
    skills: ['karpathy-guidelines', { name: 'self-checking', note: '提出前に自己評価' }, '', { bad: 1 }],
    tools: { allow: ['fs_read', 'fs_write', ''], deny_note: 'push は人の確認' },
    max_chars: 1500,
  });
  assert.strictEqual(c1.revision, 1);
  assert.strictEqual(c1.text, '回答は日本語。');
  // 文字列 / {name,note} 混在は正規化、空・不正は捨てる
  assert.deepStrictEqual(c1.skills, [
    { name: 'karpathy-guidelines' },
    { name: 'self-checking', note: '提出前に自己評価' },
  ]);
  assert.deepStrictEqual(c1.tools.allow, ['fs_read', 'fs_write']);
  assert.strictEqual(c1.tools.deny_note, 'push は人の確認');
  assert.strictEqual(c1.max_chars, 1500);
  assert.strictEqual(c1.updated_by, 'dashboard');
  // 部分更新: text だけ変えても skills/tools は保持され revision は +1
  const c2 = instructions.saveInstructions(instrCfg(dir), { text: '破壊的変更前にテスト確認。' });
  assert.strictEqual(c2.revision, 2);
  assert.strictEqual(c2.text, '破壊的変更前にテスト確認。');
  assert.deepStrictEqual(c2.skills, [
    { name: 'karpathy-guidelines' },
    { name: 'self-checking', note: '提出前に自己評価' },
  ]);
  // max_chars ハード上限 8000 でクランプ
  const c3 = instructions.saveInstructions(instrCfg(dir), { max_chars: 999999 });
  assert.strictEqual(c3.max_chars, 8000);
  // 検証: 型不一致は弾く
  assert.throws(() => instructions.saveInstructions(instrCfg(dir), { text: 123 }));
  assert.throws(() => instructions.saveInstructions(instrCfg(dir), { skills: 'x' }));
});

test('指示: renderBlock は決定的にマーカー付きブロックを描く / enabled=false と空は no-op', () => {
  const block = instructions.renderBlock({
    revision: 5,
    enabled: true,
    text: '回答は日本語。',
    skills: [{ name: 'karpathy-guidelines' }, { name: 'self-checking', note: '提出前に自己評価' }],
    tools: { allow: ['fs_read'], deny_note: 'push は人の確認' },
    max_chars: 2000,
  });
  assert.ok(block.startsWith('<!-- agent-instructions rev:5 -->\n'));
  assert.ok(block.includes('## 共通指示（agent-dashboard 管理・全ノード共通）'));
  assert.ok(block.includes('回答は日本語。'));
  assert.ok(block.includes('- karpathy-guidelines'));
  assert.ok(block.includes('- self-checking — 提出前に自己評価'));
  assert.ok(block.includes('ツール（許可）: fs_read'));
  assert.ok(block.includes('ツール方針: push は人の確認'));
  // enabled=false / 中身なしは空
  assert.strictEqual(instructions.renderBlock({ revision: 1, enabled: false, text: 'x' }), '');
  assert.strictEqual(instructions.renderBlock({ revision: 1, enabled: true, text: '   ' }), '');
});

test('指示: renderBlock は max_chars 超過で末尾切り詰めしマーカーは残す', () => {
  const long = 'あ'.repeat(500);
  const block = instructions.renderBlock({ revision: 3, enabled: true, text: long }, 80);
  assert.ok(block.length <= 80);
  assert.ok(block.startsWith('<!-- agent-instructions rev:3 -->'));
  assert.ok(block.endsWith('…'));
});

test('指示: prependBlock は二重注入を防ぐ（マーカーが既にあれば前置しない）', () => {
  const block = '<!-- agent-instructions rev:1 -->\n## 共通指示（agent-dashboard 管理・全ノード共通）\nX';
  const injected = instructions.prependBlock('タスク本文', block);
  assert.ok(injected.startsWith(block));
  assert.ok(injected.includes('タスク本文'));
  // 既にマーカーを含む文字列には前置しない
  const already = instructions.prependBlock(injected, block);
  assert.strictEqual(already, injected);
  // block が空なら target をそのまま返す
  assert.strictEqual(instructions.prependBlock('本文', ''), '本文');
});

test('指示: skillsInventory は5つのユーザースキル置き場を読み、同名と表示用メタデータをまとめる', () => {
  const home = tmpdir('orch-instr-home-');
  const kiro = path.join(home, '.kiro', 'skills', 'alpha');
  const codex = path.join(home, '.codex', 'skills', 'nested', 'alpha-copy');
  const agents = path.join(home, '.agents', 'skills', 'beta');
  fs.mkdirSync(kiro, { recursive: true });
  fs.mkdirSync(codex, { recursive: true });
  fs.mkdirSync(agents, { recursive: true });
  fs.writeFileSync(path.join(kiro, 'SKILL.md'), `---
name: alpha
description: "人向けの説明"
allowed-tools: Bash(*)
metadata:
  version: 1.2.0
  category: review
  tags:
    - quality
    - code
---
# alpha`);
  fs.writeFileSync(path.join(codex, 'SKILL.md'), '---\nname: alpha\ndescription: 後勝ちにしない\n---\n');
  fs.writeFileSync(path.join(agents, 'SKILL.md'), '---\nname: beta\ndescription: 別スキル\n---\n');

  const inv = instructions.skillsInventory({}, home);
  assert.deepStrictEqual(inv.map((s) => s.name), ['alpha', 'beta']);
  assert.strictEqual(inv[0].description, '人向けの説明');
  assert.strictEqual(inv[0].category, 'review');
  assert.strictEqual(inv[0].version, '1.2.0');
  assert.deepStrictEqual(inv[0].tags, ['quality', 'code']);
  assert.deepStrictEqual(inv[0].sources, ['kiro', 'codex']);
  assert.ok(!Object.hasOwn(inv[0], 'allowed-tools'));
});

test('手法: enable はカタログ snapshot を固定し revision を単調増加、disable/add も保存する', () => {
  const tdir = tmpdir('orch-tuning-');
  const mdir = tmpdir('orch-methods-');
  const cfg = tuningCfg(tdir, mdir);
  const method = { id: 'test-first', description: 'テストから始める', enabled: false,
    fragments: [{ role: 'worker', text: '先にテストを書く' }], when: {}, origin: 'built-in' };
  fs.writeFileSync(path.join(mdir, 'test-first.json'), JSON.stringify(method));
  const enabled = tuning.setMethod(cfg, { id: 'test-first', enabled: true });
  assert.strictEqual(enabled.revision, 1);
  assert.strictEqual(enabled.methods[0].source, `methods/test-first@${tuning.sourceHash(method)}`);
  const updatedMethod = { ...method, description: '更新' };
  fs.writeFileSync(path.join(mdir, 'test-first.json'), JSON.stringify(updatedMethod));
  assert.strictEqual(tuning.load(cfg).methods[0].description, 'テストから始める', 'カタログ更新を自動反映しない');
  const refreshed = tuning.setMethod(cfg, { id: 'test-first', enabled: true });
  assert.strictEqual(refreshed.revision, 2);
  assert.strictEqual(refreshed.methods[0].description, '更新');
  assert.strictEqual(refreshed.methods[0].source,
    `methods/test-first@${tuning.sourceHash(updatedMethod)}`);
  assert.strictEqual(tuning.setMethod(cfg, { id: 'test-first', enabled: false }).revision, 3);
  const custom = tuning.addMethod(cfg, { id: 'custom-check', description: '二重確認する', role: 'verify', text: '二重確認する',
    when: { tiers: ['full'] } });
  assert.strictEqual(custom.revision, 4);
  assert.strictEqual(custom.methods.find((item) => item.id === 'custom-check').source, 'custom/custom-check');
  assert.strictEqual(tuning.setMethod(cfg, { id: 'custom-check', enabled: false }).revision, 5);
  const reenabled = tuning.setMethod(cfg, { id: 'custom-check', enabled: true });
  assert.strictEqual(reenabled.revision, 6);
  assert.strictEqual(reenabled.methods.find((item) => item.id === 'custom-check').enabled, true,
    'カスタム手法もOFFからONへ戻せる');
  assert.throws(() => tuning.addMethod(cfg, {
    id: 'custom-check', description: '重複', role: 'worker', text: '重複',
  }), /既にあります/);
});

test('手法: 読み取り専用の定義は別IDでユーザーホームへコピーする', () => {
  const tdir = tmpdir('orch-tuning-copy-');
  const mdir = tmpdir('orch-methods-copy-');
  const cfg = tuningCfg(tdir, mdir);
  const source = { id: 'shared-check', description: '共有の確認', enabled: true,
    fragments: [{ role: 'verify', text: '共有の観点で確認する' }], when: { engines: ['agent-flow'] },
    source: 'repository:.agents/methods/shared-check.json@abc', _from: 'repository' };
  const copied = tuning.importMethod(cfg, source, 'my-shared-check');
  const method = copied.methods[0];
  assert.strictEqual(method.id, 'my-shared-check');
  assert.strictEqual(method.source, 'custom/my-shared-check');
  assert.strictEqual(method.origin, 'user');
  assert.strictEqual(method.fragments[0].text, source.fragments[0].text);
  assert.ok(!Object.prototype.hasOwnProperty.call(method, '_from'));
  assert.throws(() => tuning.importMethod(cfg, source, 'my-shared-check'), /既にあります/);
});

// 同梱カタログの source ダイジェストは Python（agent-loop）にも実装がある。
// `source: methods/<id>@<hash>` はどちらが書いても同じでなければ「同じカタログ由来か」を
// 突き合わせられないので、両側が同じ golden を読む。片方の正規化（キー順・区切り・数値
// 表記）が変わればここが落ちる。
test('手法: 同梱カタログの source ダイジェストが共有 golden と一致する', () => {
  const root = path.resolve(__dirname, '../../..');
  const golden = JSON.parse(fs.readFileSync(
    path.join(root, 'schemas', 'methods-source-hash.golden.json'), 'utf8')).hashes;
  const actual = {};
  for (const name of fs.readdirSync(path.join(root, 'methods')).filter((n) => n.endsWith('.json'))) {
    const method = JSON.parse(fs.readFileSync(path.join(root, 'methods', name), 'utf8'));
    actual[method.id] = tuning.sourceHash(method);
  }
  assert.deepStrictEqual(actual, golden);
});

// --- IPC 配線（overview がまとめて返す） -------------------------------------

test('IPC: orchestration:overview は制御面と候補適格性をまとめて返す', () => {
  const bdir = tmpdir('orch-ov-b-');
  const cdir = tmpdir('orch-ov-c-');
  const idir = tmpdir('orch-ov-i-');
  budget.save(budgetCfg(bdir), { tokens: 500000 });
  control.saveControl(controlCfg(cdir), { workloads: { routine: { lifecycle: 'run' } } });
  instructions.saveInstructions(instrCfg(idir), { text: '共通指示テスト' });
  fs.writeFileSync(path.join(cdir, 'qualifications.json'), JSON.stringify({
    version: 1, revision: 8, candidates: [{ agent_cli: 'aider', model: 'gemma4:e4b' }],
  }));
  const cfg = { orchestration: { budgetDir: bdir, controlDir: cdir, instructionsDir: idir } };
  const handlers = {};
  require('../src/features/orchestration/index.js').registerIpc({
    handle: (ch, fn) => { handlers[ch] = fn; }, loadConfig: () => cfg, saveConfig: () => cfg,
  });
  const ov = handlers['orchestration:overview']();
  assert.strictEqual(ov.budget.tokenLimit, 500000);
  assert.strictEqual(ov.control.workloads.routine.lifecycle, 'run');
  assert.ok(Array.isArray(ov.status));
  assert.ok(Array.isArray(ov.agents.dropins));
  assert.strictEqual(ov.instructions.text, '共通指示テスト');
  assert.strictEqual(ov.qualifications.revision, 8);
  assert.strictEqual(ov.qualifications.candidates[0].agent_cli, 'aider');
  assert.ok(ov.instructionsPreview.includes('共通指示テスト'));
  assert.strictEqual(ov.budgetDir, bdir);
  assert.strictEqual(ov.controlDir, cdir);
  assert.strictEqual(ov.instructionsDir, idir);
  assert.ok(Array.isArray(ov.methodsCatalog));
  for (const method of ov.methodsCatalog) {
    const { catalog_source: source, ...catalogMethod } = method;
    assert.strictEqual(source, `methods/${method.id}@${tuning.sourceHash(catalogMethod)}`);
  }
  assert.strictEqual(ov.tuning.version, 1);
  // save 動線
  const saved = handlers['orchestration:instructionsSave']({ text: '更新' });
  assert.strictEqual(saved.revision, 2);
  assert.ok(Array.isArray(handlers['orchestration:skillsInventory']()));
  assert.strictEqual(typeof handlers['orchestration:methodSet'], 'function');
  assert.strictEqual(typeof handlers['orchestration:methodAdd'], 'function');
});

console.log(`\n${passed} orchestration tests passed`);
