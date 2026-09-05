'use strict';

// Electron を実際に起動し、親画面→自動化 iframe→IPC の境界を通す。
// DOM 単体では contextBridge やフレーム間の API 参照切れを検出できないため、
// 登録済みリポジトリのワークフローが表示されるところまで確認する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..');

function electronBinary() {
  try {
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const nodePrefix = path.dirname(path.dirname(process.execPath));
  try { return require(path.join(nodePrefix, 'lib', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright-core')); } catch { /* 次を試す */ }
  try { return require('/opt/node22/lib/node_modules/playwright'); } catch { return null; }
}

test('実機: 作業から自動化へ移動し、登録済みリポジトリのフローを開ける', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (!pw || !pw._electron) { t.skip('Playwright の Electron ドライバが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い'); return; }

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-userdata-'));
  const appStore = require('../src/main/store');
  const machineStore = require('statemachine-maker/src/main/store');
  machineStore.save(repo, {
    name: 'リリース確認', machine: 'release-check', purpose: '自動化統合の確認',
    steps: [{ kind: 'agent', title: '変更を確認', detail: '公開前の変更を確認する' }],
  });
  appStore.saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'work' });

  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
    win.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

    await win.waitForSelector('#area-automation');
    await win.waitForFunction(() => typeof document.getElementById('area-automation').onclick === 'function', null, { timeout: 20000 });
    assert.match(await win.textContent('#side'), /会話.*自動化/s);
    await win.click('#area-automation');
    await win.waitForFunction(() => document.getElementById('automation-frame').getAttribute('src') !== 'about:blank');

    const automation = win.frameLocator('#automation-frame');
    await automation.locator('.execution-item').first().waitFor({ timeout: 20000 });
    assert.strictEqual(await automation.locator('.folder-list li').count(), 1, '登録リポジトリを共有できていない');
    await automation.locator('[data-home-tab="workflows"]').click();
    await automation.locator('.machine-card[data-open="release-check"]').waitFor();
    await automation.locator('.machine-card[data-open="release-check"]').click();
    await automation.locator('[data-step="0"]').waitFor();
    assert.match(await automation.locator('[data-step="0"]').textContent(), /変更を確認/);

    if (process.env.AGENT_APP_AUTOMATION_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_AUTOMATION_SCREENSHOT });
    }
    await win.click('#area-work');
    assert.strictEqual(await win.locator('#main').isVisible(), true, '会話画面へ戻れない');
    assert.deepStrictEqual(errors, [], '画面でエラーが発生した');
  } finally {
    await electron.close();
  }
});
