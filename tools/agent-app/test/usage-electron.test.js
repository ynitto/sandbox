'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const store = require('../src/main/store');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('Electron: usage, temporary allocation, manual quota, history, and existing conversation', { timeout: 120000 }, async t => {
  const pw = playwright(); if (!pw?._electron) return t.skip('Playwright unavailable');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-usage-ui-'));
  const repo = path.join(root, 'repo'), data = path.join(root, 'data');
  fs.mkdirSync(repo);
  store.saveConfig(data, { repos: [repo], lastRepo: repo, area: 'conversation', lastCli: 'claude', transport: 'headless', useWorktree: false,
    execution: { tiers: { small: { cli: 'claude' }, medium: { cli: 'claude' }, large: { cli: 'claude' } } },
    audit: { enabled: false }, share: { enabled: false }, update: { onStartup: false } });
  const session = store.createSession(data, { repo, cli: 'claude', model: 'cloud-model', policy: 'recommended', transport: 'headless' });
  store.appendMessage(data, session.id, { role: 'user', text: '会話を続けたい' });
  store.appendMessage(data, session.id, { role: 'assistant', text: '現在の会話です' });
  const app = await pw._electron.launch({ executablePath: require('electron'),
    args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`] });
  try {
    const win = await app.firstWindow(); win.setDefaultTimeout(15000);
    await win.waitForFunction(() => typeof document.getElementById('usage-open')?.onclick === 'function');
    await app.evaluate(({ ipcMain }) => {
      const handle = (name, value) => { ipcMain.removeHandler(name); ipcMain.handle(name, () => ({ ok: true, data: value })); };
      const limits = [{ agent_cli: 'claude', quota_used_percent: 92, observed_at: new Date().toISOString(), reset_at: new Date(Date.now() + 7200000).toISOString() },
        { agent_cli: 'codex', quota_used_percent: 42, observed_at: new Date().toISOString(), reset_at: new Date(Date.now() + 14400000).toISOString() }];
      handle('agents:list', [{ name: 'claude', available: true }, { name: 'codex', available: true }, { name: 'herd', command: 'agent-herd', virtual: true, available: true }]);
      handle('audit:limits', { available: true, agentLimits: limits });
      handle('audit:status', { available: true, running: false });
      handle('audit:summary', { available: true, agentLimits: limits, by: 'workload', totals: { measured_in: 8000, measured_out: 2000, estimated_tokens: 100, runs: 10, unmeasured_runs: 1 },
        allocationUsage: { local: { runs: 6 }, cloud: { runs: 4, tokens: 3000, unmeasured: 1 }, other: { runs: 0 }, localPercent: 60, runs: 10 },
        quality: { ledger: { runs: 10, pass_rate: 0.8, status: { done: 8 } } },
        usage: { rows: [{ group: 'chat', runs: 8, measured_in: 6000, measured_out: 1500 }, { group: 'evaluation', runs: 2, measured_in: 2000, measured_out: 500 }],
          evaluation: { evaluations: 5, quality_avg: 2.4, issues: 2 } } });
    });
    await win.reload();
    const errors = []; win.on('pageerror', e => errors.push(e.message));
    await win.waitForFunction(() => typeof document.getElementById('usage-open')?.onclick === 'function');
    await win.waitForFunction(() => !state.agentsLoading);
    await win.evaluate(({ repo, id }) => openSessionInRepo(repo, id), { repo, id: session.id });
    await win.fill('#prompt', '書きかけを保持');
    // 会話では利用状況の入口は実行設定の中（ヘッダーのボタンはホーム専用）
    await win.locator('#run-settings summary').click();
    await win.click('#run-usage-open');
    await win.waitForFunction(() => document.getElementById('audit-limits').textContent.includes('残り 8%'));
    await win.waitForFunction(() => document.getElementById('audit-usage').textContent.includes('60%'));
    assert.equal(await win.locator('dialog[open]').count(), 1);
    assert.equal(await win.locator('[data-settings-panel="audit"] details').count(), 0);
    assert.equal(await win.locator('#audit-evaluation').count(), 0);
    assert.equal(await win.locator('[data-settings-panel="audit"] #usage-mode').count(), 0);
    assert.match(await win.textContent('#audit-breakdown'), /入力 6k/);
    await win.click('#usage-execution');
    assert.equal(await win.getByRole('tab', { name: '実行制御', exact: true }).getAttribute('aria-selected'), 'true');
    await win.fill('#usage-local-model', 'draft-local');
    await win.selectOption('#usage-until', 'manual');
    assert.equal(store.loadConfig(data).allocation.temporary, null, 'allocation remains a draft before save');
    await win.evaluate(() => { Audit.renderLimits(); Audit.renderAllocation(); });
    assert.equal(await win.inputValue('#usage-local-model'), 'draft-local');
    await win.getByRole('tab', { name: '共通指示', exact: true }).click();
    await win.fill('#instruction-text', '未保存の設定を保持');
    await win.getByRole('tab', { name: '実行制御', exact: true }).click();
    await win.click('#execution-usage-open');
    assert.equal(await win.inputValue('#instruction-text'), '未保存の設定を保持');
    await win.click('[data-manual-agent="kiro"]');
    await win.fill('#usage-manual-remaining', '25');
    await win.fill('#usage-manual-reset', '2030-10-01T10:00');
    assert.equal(await win.locator('#usage-manual-row .setting-field').count(), 2);
    assert.equal(await win.textContent('#usage-execution'), '実行制御を変更 →');
    await win.screenshot({ path: '/tmp/agent-app-manual-quota.png' });
    await win.setViewportSize({ width: 375, height: 900 });
    assert.equal(await win.locator('.settings-content').evaluate(n => n.scrollWidth <= n.clientWidth + 1), true);
    await win.screenshot({ path: '/tmp/agent-app-manual-quota-narrow.png' });
    await win.setViewportSize({ width: 1280, height: 900 });
    await win.evaluate(() => Audit.renderLimits());
    assert.equal(await win.inputValue('#usage-manual-remaining'), '25');
    await win.click('#usage-manual-save');
    await win.waitForFunction(() => document.getElementById('audit-limits').textContent.includes('残り 25%（手動）'));
    assert.equal(store.loadConfig(data).audit.manualLimits[0].quota_used_percent, 75);
    await win.click('#usage-execution');
    assert.equal(await win.inputValue('#usage-local-model'), 'draft-local', 'recording quota preserves allocation draft');
    assert.equal(await win.inputValue('#usage-until'), 'manual');
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    assert.equal(store.loadConfig(data).allocation.temporary.until, null);
    assert.equal(store.loadConfig(data).allocation.localModel, 'draft-local');
    assert.match(await win.textContent('#usage-allocation-state'), /ローカル/);
    await win.locator('#usage-mode').scrollIntoViewIfNeeded();
    await win.screenshot({ path: '/tmp/agent-app-execution-redesign.png' });
    await win.click('#settings-close');
    assert.equal(await win.inputValue('#prompt'), '書きかけを保持');
    assert.match(await win.textContent('#run-settings-summary'), /claude/);
    await win.click('#session-new');
    assert.match(await win.textContent('#run-settings-summary'), /ローカル/);
    await win.click('#run-settings > summary');
    // 今回だけローカル優先から戻すのは、選択方法を手動指定にしてエージェントを選ぶ操作
    // （#run-allocation は常時隠しになっている）
    await win.selectOption('#run-settings [data-execution-mode]', 'manual');
    await win.selectOption('#cli', 'claude');
    assert.match(await win.textContent('#run-settings-summary'), /claude/);
    await win.click('#run-usage-open');
    await win.click('#usage-execution');
    await win.selectOption('#usage-until', 'off');
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    assert.equal(store.loadConfig(data).allocation.temporary, null);
    await win.selectOption('#usage-until', 'reset');
    const deadline = await win.inputValue('#usage-reset-agent');
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    assert.equal(store.loadConfig(data).allocation.temporary.until, deadline);
    await win.click('#settings-save');
    await win.waitForFunction(() => !document.getElementById('settings-save').disabled);
    assert.equal(store.loadConfig(data).allocation.temporary.until, deadline, 'unrelated save does not extend a temporary deadline');
    await win.fill('#usage-local-model', 'discard-this');
    await win.click('#settings-close');
    await win.locator('#run-settings summary').click();
    await win.click('#run-usage-open');
    await win.click('#usage-execution');
    assert.equal(await win.inputValue('#usage-local-model'), 'draft-local', 'closing without saving discards allocation edits');
    await win.click('#execution-usage-open');
    await win.waitForFunction(() => document.getElementById('audit-usage').textContent.includes('60%'));
    await win.screenshot({ path: '/tmp/agent-app-usage-redesign.png' });
    await win.locator('#audit-breakdown').scrollIntoViewIfNeeded();
    await win.screenshot({ path: '/tmp/agent-app-usage-redesign-breakdown.png' });
    for (const width of [375, 768, 1024, 1440]) {
      await win.setViewportSize({ width, height: 900 });
      for (const name of ['実行制御', '利用状況']) {
        await win.getByRole('tab', { name, exact: true }).click();
        assert.equal(await win.locator('.settings-content').evaluate(n => n.scrollWidth <= n.clientWidth + 1), true, `${name} fits ${width}px`);
        if (width === 375) await win.screenshot({ path: `/tmp/agent-app-${name === '実行制御' ? 'execution' : 'usage'}-redesign-narrow.png` });
      }
    }
    assert.equal(await win.locator('#usage-issues').count(), 0);
    assert.equal(await win.locator('dialog[open]').count(), 1);
    await win.click('#settings-close');
    assert.deepEqual(errors, []);
  } finally { await app.close(); fs.rmSync(root, { recursive: true, force: true }); }
});
