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

test('監査タブは index.html に配線され、feature スクリプトを bootstrap より先に読む', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.match(html, /data-tab="agent-audit"[^>]*hidden>監査<\/button>/);
  assert.match(html, /id="tab-agent-audit"[^>]*hidden/);
  const feature = html.indexOf('features/agent-audit.js');
  assert.ok(feature > 0);
  assert.ok(feature < html.indexOf('bootstrap.js'), 'bootstrap より先に読み込む');
});
