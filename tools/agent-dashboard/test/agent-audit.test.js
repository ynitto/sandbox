'use strict';

// 監査タブ（agent-audit 呼び出し）のテスト。Electron は起動せず、
// コマンド組み立て・JSON 契約の読み取り・collect の直列化・画面 HTML を検証する。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const audit = require('../src/features/agent-audit/main/audit');
const ui = require('../src/renderer/features/agent-audit');

const okResult = (stdout) => ({ ok: true, status: 0, stdout, stderr: '', error: '' });

test('buildScript はグローバル引数をサブコマンドの前に置き、値を引用する', () => {
  const settings = audit.auditSettings({
    agentAudit: {
      command: 'python3 ~/repo/tools/agent-audit/agent-audit.py',
      configPath: '~/.agents/agent-audit.json',
      auditDir: "~/audit dir/with'quote",
    },
  });
  const script = audit.buildScript(settings, ['collect']);
  assert.equal(script,
    "python3 ~/repo/tools/agent-audit/agent-audit.py "
    + "'--config' '~/.agents/agent-audit.json' "
    + "'--audit-dir' '~/audit dir/with'\"'\"'quote' 'collect'");
});

test('buildScript はコマンド未設定なら PATH の agent-audit を探す', () => {
  const script = audit.buildScript(audit.auditSettings({}), ['usage', '--json']);
  assert.match(script, /command -v agent-audit/);
  assert.match(script, /"\$bin" 'usage' '--json'/);
  assert.match(script, /exit 127/);
});

test('parseJson はログインシェルの前置きが混ざっても JSON 本体を取り出す', () => {
  const parsed = audit.parseJson('profile says hi\n{"period":"month",\n "rows":[]}\n');
  assert.deepEqual(parsed, { period: 'month', rows: [] });
  assert.throws(() => audit.parseJson('JSON なしの出力'), /JSON がありません/);
});

test('usage は不正な期間・軸を既定へ寄せ、--json の応答を返す', async () => {
  const scripts = [];
  const data = await audit.usage({}, 'bogus', 'bogus', async (script) => {
    scripts.push(script);
    return okResult('{"period":"month","by":"workload","rows":[{"group":"flow","runs":1}]}');
  });
  assert.match(scripts[0], /'usage' '--period' 'month' '--by' 'workload' '--json'/);
  assert.equal(data.rows.length, 1);
  assert.equal(data.rows[0].group, 'flow');
});

test('usage は失敗時に stderr を理由として投げる', async () => {
  await assert.rejects(
    audit.usage({}, 'month', 'agent_cli', async () => (
      { ok: false, status: 2, stdout: '', stderr: '源泉が読めません', error: '' })),
    /源泉が読めません/);
});

test('stats は --json の応答をそのまま返す', async () => {
  const data = await audit.stats({}, 'total', async (script) => {
    assert.match(script, /'stats' '--period' 'total' '--json'/);
    return okResult('{"period":"total","tools":[]}');
  });
  assert.deepEqual(data, { period: 'total', tools: [] });
});

test('sessions は絞り込み引数を組み立て、JSON の一覧を返す', async () => {
  const data = await audit.sessions({}, {
    cli: 'claude',
    sinceIso: '2026-08-03T10:00:00Z',
    untilIso: '2026-08-03T11:00:00Z',
    nativeId: 's-1',
    limit: 1,
  }, async (script) => {
    assert.match(script, /'sessions' '--cli' 'claude' '--since' '2026-08-03T10:00:00Z'/);
    assert.match(script, /'--until' '2026-08-03T11:00:00Z' '--messages' 's-1' '--limit' '1'/);
    return okResult('{"sessions":[{"native_id":"s-1","messages":[{"role":"User","text":"直して"}]}]}');
  });
  assert.equal(data.sessions[0].native_id, 's-1');
  assert.equal(data.sessions[0].messages[0].role, 'User');
});

test('sessions は失敗時に stderr を理由として投げる', async () => {
  await assert.rejects(
    audit.sessions({}, { cli: 'claude' }, async () => (
      { ok: false, status: 2, stdout: '', stderr: 'agent-audit が見つかりません', error: '' })),
    /agent-audit が見つかりません/);
});

test('doctor は非ゼロ終了でも本文を返し、投げない', async () => {
  const result = await audit.doctor({}, async () => (
    { ok: false, status: 2, stdout: '点検: 源泉に届きません', stderr: '', error: '' }));
  assert.equal(result.ok, false);
  assert.match(result.detail, /届きません/);
  assert.match(result.error, /exit 2/);
});

test('collect は多重実行をビジーとして直列化する', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const first = audit.collect({}, async () => {
    await gate;
    return okResult('新規レコード 0 件');
  });
  const second = await audit.collect({}, async () => okResult(''));
  assert.equal(second.busy, true);
  assert.match(second.error, /実行中/);
  release();
  const result = await first;
  assert.equal(result.ok, true);
  assert.equal(result.busy, false);
  assert.ok(result.at);
  assert.match(result.detail, /新規レコード 0 件/);
});

test('利用量テーブルは実測と推定を別の列で示し合算しない', () => {
  const html = ui.usageTableHtml({
    period: 'month',
    by: 'agent_cli',
    rows: [{
      group: 'claude', runs: 3, seconds: 5400, measured_in: 1200000, measured_out: 34000,
      estimated_tokens: 5000, unmeasured_runs: 1, usd: 0.05,
    }],
  });
  assert.match(html, /実測トークン 入力/);
  assert.match(html, /実測トークン 出力/);
  assert.match(html, /推定トークン/);
  assert.match(html, /1\.20M/);
  assert.match(html, /34k/);
  assert.match(html, /1\.5時間/);
  assert.match(html, /\$0\.05/);
  assert.match(html, /合算していません/);
});

test('利用量テーブルは記録が無いとき収集への導線を示す', () => {
  assert.match(ui.usageTableHtml({ rows: [] }), /今すぐ収集/);
  assert.match(ui.usageTableHtml(null), /今すぐ収集/);
});

test('summary は機能別とエージェント別を 1 回で取り、合計を畳む', async () => {
  const scripts = [];
  const data = await audit.summary({}, 'day', async (script) => {
    scripts.push(script);
    const rows = script.includes("'workload'")
      ? [{ group: 'flow', runs: 2, seconds: 120, measured_in: 1000, measured_out: 500,
           estimated_tokens: 300, unmeasured_runs: 1, usd: 0.25 },
         { group: 'project', runs: 1, seconds: 60, measured_in: 200, measured_out: 100,
           estimated_tokens: 0, unmeasured_runs: 0, usd: 0 }]
      : [{ group: 'claude', runs: 3, seconds: 180, measured_in: 1200, measured_out: 600,
           estimated_tokens: 300, unmeasured_runs: 1, usd: 0.25 }];
    return okResult(JSON.stringify({ period: 'day', rows }));
  });
  assert.match(scripts[0], /'--period' 'day' '--by' 'workload'/);
  assert.match(scripts[1], /'--period' 'day' '--by' 'agent_cli'/);
  assert.equal(data.period, 'day');
  assert.equal(data.workloads.length, 2);
  assert.equal(data.agents.length, 1);
  // 実測と推定は別々に畳み、合算値は total にだけ持つ（列の意味づけは agent-audit の契約）
  assert.equal(data.totals.measured, 1800);
  assert.equal(data.totals.estimated, 300);
  assert.equal(data.totals.total, 2100);
  assert.equal(data.totals.runs, 3);
  assert.equal(data.totals.seconds, 180);
  assert.equal(data.totals.unmeasuredRuns, 1);
  assert.equal(data.totals.usd, 0.25);
});

const SUMMARY = {
  period: 'month',
  workloads: [{ group: 'flow', runs: 2, seconds: 120, measured_in: 2000, measured_out: 1000,
                estimated_tokens: 300, unmeasured_runs: 0, usd: 0.25 }],
  agents: [{ group: 'claude', runs: 2, seconds: 120, measured_in: 2000, measured_out: 1000,
             estimated_tokens: 300, unmeasured_runs: 0, usd: 0.25 }],
  totals: { runs: 2, seconds: 120, measuredIn: 2000, measuredOut: 1000, measured: 3000,
            estimated: 300, total: 3300, unmeasuredRuns: 0, usd: 0.25 },
};

function withState(state, fn) {
  globalThis.state = state;
  try {
    return fn();
  } finally {
    delete globalThis.state;
  }
}

test('機能別テーブルは agent-audit の集計に予算の上限と状態を重ねる', () => {
  const html = withState({
    orchestration: {
      budget: {
        knownWorkloads: ['routine', 'project', 'flow', 'amigos'],
        tokenLimit: 10000,
        config: { period: 'month' },
        workloads: { flow: { tokenCap: 5000, soft: true } },
      },
    },
  }, () => ui.workloadTableHtml(SUMMARY));
  assert.match(html, /実測 3k/);                  // 実測と推定は別々に示す（合算しない）
  assert.match(html, /推定 300/);
  assert.match(html, /上限 5k/);                  // 枠はノード予算が正
  assert.match(html, /節約モード/);
  assert.match(html, /記録なし/);                 // 記録の無い機能も枠が見えるよう並べる
});

// 棒グラフは長い順に並んでいないと比べられない。実行数が多くてもトークンが少ない行は下へ。
test('割合バーのある表は割合の大きい順に並ぶ（機能別・エージェント別とも）', () => {
  const agents = withState({}, () => ui.agentTableHtml({
    totals: { total: 10000 },
    agents: [{ group: 'many-runs', runs: 50, seconds: 10, measured_in: 10, measured_out: 10,
               estimated_tokens: 0, usd: 0 },
             { group: 'big-tokens', runs: 1, seconds: 10, measured_in: 4000, measured_out: 4000,
               estimated_tokens: 0, usd: 0 }],
  }));
  assert.ok(agents.indexOf('big-tokens') < agents.indexOf('many-runs'),
    '実行数ではなく割合で並べる');

  const workloads = withState({
    orchestration: { budget: { knownWorkloads: ['routine', 'flow'], config: { period: 'month' }, workloads: {} } },
  }, () => ui.workloadTableHtml({
    totals: { total: 10000 },
    workloads: [{ group: 'routine', runs: 9, measured_in: 100, measured_out: 0, estimated_tokens: 0 },
                { group: 'flow', runs: 1, measured_in: 5000, measured_out: 1000, estimated_tokens: 0 }],
  }));
  assert.ok(workloads.indexOf('タスク実行') < workloads.indexOf('定常業務')
    || workloads.indexOf('flow') < workloads.indexOf('routine'), '機能別も割合の大きい順');
});

test('ゲージは期間が予算の期間と一致するときだけ残量を出す', () => {
  const budget = (period) => ({
    orchestration: { budget: { tokenLimit: 10000, config: { period } } },
  });
  const same = withState(budget('month'), () => ui.gaugeHtml(SUMMARY.totals, 'month'));
  assert.match(same, /33\.0% 使用/);
  assert.match(same, /残り 7k/);
  // 期間がずれたら残量は出さない（別期間の集計に上限を重ねると嘘の残量になる）
  const other = withState(budget('day'), () => ui.gaugeHtml(SUMMARY.totals, 'month'));
  assert.doesNotMatch(other, /使用/);
  assert.match(other, /期間を合わせて/);
  // 上限未設定でも集計は続く
  assert.match(withState({}, () => ui.gaugeHtml(SUMMARY.totals, 'month')), /上限は設定されていません/);
});

test('エージェント別テーブルは割合の大きい順に並べ、概算費用を添える', () => {
  const html = withState({}, () => ui.agentTableHtml({
    totals: { total: 3300 },
    agents: [{ group: 'ollama', runs: 1, seconds: 30, measured_in: 0, measured_out: 0,
               estimated_tokens: 0, unmeasured_runs: 1, usd: 0 },
             { group: 'claude', runs: 5, seconds: 300, measured_in: 1000, measured_out: 500,
               estimated_tokens: 300, unmeasured_runs: 0, usd: 0.25 }],
  }));
  assert.ok(html.indexOf('claude') < html.indexOf('ollama'), '割合の大きい順');
  assert.match(html, /取得できず/);               // トークン不明でも時間と件数は数える
  assert.match(html, /\$0\.25/);
});

test('agent-audit の集計が取れない端末では台帳集計へフォールバックし、その旨を言う', () => {
  const html = withState({ orchestration: { budget: { totalTokens: {} } } }, () => {
    globalThis.orchBudgetPanelHtml = () => '<section id="ledger-panel"></section>';
    try {
      return ui.ledgerFallbackHtml();
    } finally {
      delete globalThis.orchBudgetPanelHtml;
    }
  });
  assert.match(html, /実行記録（台帳）だけの集計/);
  assert.match(html, /id="ledger-panel"/);
  // 予算も読めなければ、数字を捏造せず設定の確認へ誘導する
  assert.match(withState({}, () => ui.ledgerFallbackHtml()), /収集の設定/);
});

test('実行品質テーブルは結果・リトライ・検証を利用者向けの言葉で示す', () => {
  const html = ui.statsTableHtml({
    period: 'month',
    tools: [{
      tool: 'agent-flow', runs: 42, status: { done: 38, failed: 4 },
      error_class: { transient: 3 }, retries: 7, verify_pass: 36, verify_fail: 2,
    }],
  });
  assert.match(html, /agent-flow/);
  assert.match(html, /完了 38 \/ 失敗 4/);
  assert.match(html, /合格 36 \/ 不合格 2/);
  assert.doesNotMatch(html, /verify_pass|error_class/);
});

test('設定フォームは保存先・設定ファイル・間隔・コマンド・ディストロを編集できる', () => {
  const html = ui.settingsHtml({
    command: '', distro: 'Ubuntu', configPath: '', auditDir: '~/.agents/audit', collectIntervalMin: 60,
  });
  for (const id of ['audit-set-command', 'audit-set-distro', 'audit-set-config',
    'audit-set-dir', 'audit-set-interval']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /value="Ubuntu"/);
  assert.match(html, /value="60"/);
  assert.match(html, /id="audit-save"/);
});

test('refresh は間隔が設定されているときだけ定期収集を起動する', async () => {
  let calls = 0;
  globalThis.state = { config: { agentAudit: { collectIntervalMin: 0 } } };
  globalThis.api = { agentAuditCollect: async () => { calls += 1; return { ok: true }; } };
  try {
    ui.refresh();
    assert.equal(calls, 0);
    globalThis.state.config.agentAudit.collectIntervalMin = 60;
    ui.refresh();
    assert.equal(calls, 1);
    ui.refresh();
    assert.equal(calls, 1, '間隔内の再ポーリングでは起動しない');
    await Promise.resolve();
  } finally {
    delete globalThis.state;
    delete globalThis.api;
  }
});

test('監査は利用状況領域の「利用量」と「設定」に分かれ、feature スクリプトを bootstrap より先に読む', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  // 画面の名前は利用者の言葉（利用状況）で、実装名（agent-audit）をタブに出さない。
  assert.doesNotMatch(html, /data-tab="agent-audit"/);
  assert.doesNotMatch(html, /id="tab-agent-audit"/);
  // 見る画面と決める画面を分ける（他の領域と同じ形）。タブは領域名を繰り返さない。
  assert.match(html, /data-tab="usage"[^>]*data-area="usage"[^>]*>利用量</);
  assert.match(html, /data-tab="usage-settings"[\s\S]{0,80}data-area="usage"/);
  assert.match(html, /id="tab-usage-settings"/);
  const feature = html.indexOf('features/agent-audit.js');
  assert.ok(feature > 0);
  assert.ok(feature < html.indexOf('bootstrap.js'), 'bootstrap より先に読み込む');
});

test('監査は「利用状況」領域へ差し込まれる', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'features', 'agent-audit.js'), 'utf8');
  // 差し込みの仕組みは変えていない（登録先のキーは 'usage' のまま）。
  assert.match(src, /registerGlobalSettingsPanel\('usage', \{/);
  assert.match(src, /id: 'agent-audit'/);
  assert.doesNotMatch(src, /registerFeatureTab/);
  // 受け側だけが全体設定から利用状況領域へ移った。ナビが領域単位になり、
  // 「プロジェクトごとの数字ではないから独立タブを持てない」という制約が無くなったため。
  const usage = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'sections', 'usage.js'), 'utf8');
  assert.match(usage, /function renderUsage[\s\S]*globalSettingsPanelsHtml\('usage'\)/);
  assert.match(usage, /function renderUsageSettings[\s\S]*globalSettingsPanelsHtml\('usage-settings'\)/);
  assert.match(src, /registerGlobalSettingsPanel\('usage-settings', \{/);
  const orch = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'renderer', 'sections', 'orchestration.js'), 'utf8');
  assert.doesNotMatch(orch, /globalSettingsPanelsHtml\('usage'\)/,
    '全体設定は利用状況の面を持たない（同じ面を 2 か所に置かない）');
});

test('差し込まれた面は登録・描画・表示通知の一巡で動く', () => {
  // renderer コアの登録簿を実際に動かし、面が自分の容れ物だけを描き直せることを確かめる。
  const core = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
  const at = core.indexOf('const globalSettingsPanels = new Map();');
  assert.ok(at > 0, 'renderer.js に全体設定の登録簿がある');
  const end = core.indexOf('function renderCliChatButton');
  // eslint-disable-next-line no-new-func
  const registry = new Function('globalThis', `${core.slice(at, end)}
    return { registerGlobalSettingsPanel, globalSettingsPanelsHtml,
             wireGlobalSettingsPanels, revealGlobalSettingsPanels };`)({});
  const seen = { wired: 0, revealed: 0 };
  registry.registerGlobalSettingsPanel('usage', {
    id: 'demo',
    html: () => '<p>面</p>',
    wire: () => { seen.wired += 1; },
    reveal: () => { seen.revealed += 1; },
  });
  const html = registry.globalSettingsPanelsHtml('usage');
  assert.match(html, /id="global-settings-slot-demo"/);
  assert.match(html, /<p>面<\/p>/);
  assert.equal(registry.globalSettingsPanelsHtml('agents'), '', '他の節へは出さない');
  const container = {};
  const root = { querySelector: (sel) => (sel === '#global-settings-slot-demo' ? container : null) };
  registry.wireGlobalSettingsPanels(root);
  registry.revealGlobalSettingsPanels(root, 'usage');
  registry.revealGlobalSettingsPanels(root, 'agents');
  assert.deepEqual(seen, { wired: 1, revealed: 1 });
});
