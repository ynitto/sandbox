'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
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


test('manual run button displays a terminal, preserves it across tabs, and sends keys and stop', { timeout: 30000 }, async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary || !pw?._electron) return t.skip('Electron / Playwright が無い');
  if (process.platform === 'linux' && !process.env.DISPLAY) return t.skip('表示先が無い');
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-ui-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-ui-user-'));
  require('../src/main/automation/store').save(repo, { name: 'Terminal task', machine: 'terminal-task', purpose: 'test',
    steps: [{ kind: 'agent', title: 'test', detail: 'test' }] });
  require('../src/main/store').saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'tasks', automationAgent: 'kiro' });
  const electron = await pw._electron.launch({ executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`] });
  t.after(async () => { await electron.close(); fs.rmSync(repo, { recursive: true, force: true }); fs.rmSync(userData, { recursive: true, force: true }); });
  await electron.evaluate(({ ipcMain }) => {
    global.manualTestCalls = [];
    const register = (name, fn) => {
      ipcMain.removeHandler(`automation:${name}`);
      ipcMain.handle(`automation:${name}`, async (event, p) => ({ ok: true, data: await fn(event, p) }));
    };
    register('agents:list', () => ['kiro']);
    register('run:start', (event) => {
      const requestId = 'manual-ui-test';
      global.manualTestSender = event.sender;
      setTimeout(() => event.sender.send('automation:run:screen', {
        id: requestId, requestId, text: 'VISIBLE MANUAL TERMINAL', cursor: { x: 0, y: 1 },
      }), 100);
      return { requestId, transport: 'tmux' };
    });
    register('run:resize', () => true);
    register('run:scroll', (event, p) => {
      global.manualTestCalls.push('scroll');
      event.sender.send('automation:run:screen', { id: p.requestId, requestId: p.requestId,
        text: 'COMPLETED TERMINAL HISTORY', cursor: { x: 0, y: 0 }, scrollOffset: 10 });
    });
    register('run:keys', (_event, p) => { global.manualTestCalls.push(p.data); });
    register('run:stop', (event) => {
      global.manualTestCalls.push('stop');
      event.sender.send('automation:run:exit', { requestId: 'manual-ui-test', code: 1, mode: 'run', result: { ok: false } });
    });
  });
  const win = await electron.firstWindow();
  const errors = [];
  win.on('pageerror', (error) => errors.push(error.message));
  await win.reload();
  await win.click('#area-tasks');
  const workspace = win.locator('#automation-workbench');
  await workspace.locator('#run-start').click();
  const terminal = win.locator('[slot="task-run-terminal"]');
  await terminal.locator('.xterm-screen').waitFor();
  await win.waitForFunction(() => document.querySelector('[slot="task-run-terminal"] .xterm-rows')?.textContent.includes('VISIBLE MANUAL TERMINAL'));
  assert.ok((await terminal.boundingBox()).height >= 200);
  assert.equal(await workspace.locator('#run-log').isVisible(), false, '端末表示中は空のログ欄を隠す');
  await workspace.getByRole('tab', { name: '履歴', exact: true }).click();
  await workspace.getByRole('tab', { name: '概要', exact: true }).click();
  assert.match(await terminal.locator('.xterm-rows').textContent(), /VISIBLE MANUAL TERMINAL/);
  assert.equal(await workspace.locator('#run-log').isVisible(), false, 'タブを戻っても実行前の案内を表示しない');
  await electron.evaluate(() => global.manualTestSender.send('automation:run:line', {
    requestId: 'manual-ui-test', kind: 'stderr', line: 'manual run warning',
  }));
  await workspace.locator('#run-log-details').waitFor({ state: 'visible' });
  assert.equal(await workspace.locator('#run-log').isVisible(), false, '警告が届いてもログは自動で展開しない');
  assert.match(await workspace.locator('#run-log-details summary').textContent(), /警告・エラーあり/);
  await workspace.locator('#run-log-details summary').click();
  await workspace.locator('#run-log').waitFor({ state: 'visible' });
  assert.equal(await workspace.locator('#run-log').textContent(), 'manual run warning');
  await workspace.locator('#run-log-details summary').click();
  await terminal.locator('textarea').focus();
  await win.keyboard.type('yes');
  await win.keyboard.press('Enter');
  await workspace.locator('#run-stop').click();
  const calls = await electron.evaluate(() => global.manualTestCalls);
  assert.ok(calls.join('').includes('yes\r'), JSON.stringify(calls));
  assert.ok(calls.includes('stop'));
  await workspace.locator('#run-start').waitFor();
  assert.equal(await workspace.locator('#run-log').isVisible(), false, '終了後に二つ目の端末状ログ欄を自動表示しない');
  await workspace.locator('#run-start').click();
  await terminal.locator('.xterm-screen').waitFor();
  await electron.evaluate(() => {
    global.manualTestSender.send('automation:run:line', { requestId: 'manual-ui-test', kind: 'stdout', line: 'task done' });
    global.manualTestSender.send('automation:run:exit', { requestId: 'manual-ui-test', mode: 'run', code: 0, result: { ok: true } });
  });
  await workspace.getByText('実行が完了しました', { exact: true }).waitFor();
  assert.equal(await workspace.locator('#run-log').isVisible(), false, '正常終了後もログは閉じたまま');
  assert.equal(await terminal.isVisible(), true, '終了時の端末画面を残す');
  await terminal.locator('.xterm-screen').hover();
  await win.mouse.wheel(0, -200);
  await win.waitForFunction(() => document.querySelector('[slot="task-run-terminal"] .xterm-rows')?.textContent.includes('COMPLETED TERMINAL HISTORY'));
  assert.equal(await workspace.locator('#toast').isVisible(), false, '終了後のスクロールでエラーを表示しない');
  await workspace.getByRole('tab', { name: '履歴', exact: true }).click();
  await workspace.getByRole('tab', { name: '概要', exact: true }).click();
  assert.equal(await workspace.locator('#run-log').isVisible(), false, '再描画でもログを自動展開しない');
  await workspace.locator('#run-log-details summary').click();
  assert.match(await workspace.locator('#run-log').textContent(), /task done/);
  assert.deepEqual(errors, []);
});
