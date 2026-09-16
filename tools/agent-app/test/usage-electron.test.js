'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const store = require('../src/main/store');

function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('Electron: 利用枠・未取得・期間切替・折りたたみ・狭幅', async t => {
  const pw = playwright(); if (!pw?._electron) return t.skip('Playwright unavailable');
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'usage-ui-'));
  store.saveConfig(data, { audit: { enabled: false }, share: { enabled: false } });
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`] });
  try {
    const win = await app.firstWindow();
    const errors = []; win.on('pageerror', e => errors.push(e.message));
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function');
    await app.evaluate(({ ipcMain }) => {
      ipcMain.removeHandler('audit:summary');
      ipcMain.handle('audit:summary', async (_event, { by, period }) => {
        if (period === 'day') await new Promise(resolve => setTimeout(resolve, 300));
        return { ok: true, data: {
          available: true, by, period,
          totals: { measured_in: 100000, measured_out: 24000, estimated_tokens: 8000, unmeasured_runs: 2, runs: 20 },
          usage: { rows: [{ group: by === 'model' ? 'example-model' : 'claude', runs: 20, measured_in: 100000, measured_out: 24000, estimated_tokens: 8000, unmeasured_runs: 2 }] },
          quality: { ledger: { runs: 20, status: { done: 19, cancelled: 1 }, pass_rate: 0.95 } },
          agentLimits: [
            { agent_cli: 'claude', quota_used_percent: 60, reset_at: new Date(Date.now() + 3600000).toISOString(), reset_source: 'observed', observed_at: new Date().toISOString() },
            { agent_cli: 'codex', quota_used_percent: 0, reset_at: new Date(Date.now() + 7200000).toISOString(), reset_source: 'observed' },
            { agent_cli: 'copilot', quota_used_percent: null },
            { agent_cli: 'kiro', quota_used_percent: 100, reset_at: '2020-01-01T00:00:00Z' },
          ],
        } };
      });
    });
    await win.click('#settings-open');
    await win.getByRole('tab', { name: '利用状況', exact: true }).click();
    await win.getByText('60% 使用', { exact: true }).waitFor();
    assert.match(await win.textContent('#audit-limits'), /0% 使用/);
    assert.match(await win.textContent('#audit-limits'), /取得できず/);
    assert.match(await win.textContent('#audit-limits'), /更新待ち/);
    assert.equal(await win.locator('#audit-limits progress').count(), 2);
    assert.match(await win.textContent('#audit-usage'), /124k/);
    assert.match(await win.textContent('#audit-usage'), /95%（19 \/ 20 件）/);
    assert.equal(await win.locator('#audit-enabled').isVisible(), false);
    await win.screenshot({ path: '/tmp/agent-app-usage.png' });
    await win.selectOption('#audit-period', 'day');
    await win.selectOption('#audit-period', 'total');
    await win.selectOption('#audit-by', 'model');
    await win.getByText('example-model', { exact: true }).waitFor();
    await win.waitForTimeout(400);
    assert.match(await win.textContent('#audit-breakdown'), /example-model/);
    await win.getByText('収集・共有の設定', { exact: true }).click();
    assert.equal(await win.locator('#audit-enabled').isVisible(), true);
    await win.fill('#audit-share-repo', 'git@example:team/skills.git');
    assert.equal(await win.locator('#audit-push-main').isVisible(), true);
    await win.getByText('収集・共有の設定', { exact: true }).click();
    await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.setMinimumSize(400, 400); w.setSize(520, 800); });
    await win.screenshot({ path: '/tmp/agent-app-usage-narrow.png' });
    assert.equal(await win.locator('.settings-content').evaluate(n => n.scrollWidth <= n.clientWidth), true);
    assert.deepEqual(errors, []);
  } finally { await app.close(); fs.rmSync(data, { recursive: true, force: true }); }
});
