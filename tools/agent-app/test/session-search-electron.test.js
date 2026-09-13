'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const store = require('../src/main/store');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}
test('Electron: global search, VS Code import, fork boundary, editable target controls and fresh session', async t => {
  const pw = playwright(); if (!pw?._electron) return t.skip('Playwright unavailable');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'session-search-ui-'));
  const data = path.join(root, 'data'), defs = path.join(root, 'agents'), repo = path.join(root, 'repo');
  fs.mkdirSync(defs); fs.mkdirSync(repo);
  const summary = path.join(root, 'summary.py');
  fs.writeFileSync(summary, 'import sys\nsys.stdin.read()\nprint("目的: 月次集計。次の作業: 結果を確認する。")\n');
  const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../agents/claude.json')));
  spec.command = ['python3', summary];
  fs.writeFileSync(path.join(defs, 'claude.json'), JSON.stringify(spec));
  store.saveConfig(data, { repos: [repo], lastRepo: repo, transport: 'headless', useWorktree: false, share: { enabled: false } });
  const original = store.createSession(data, { repo, cli: 'claude', transport: 'headless' });
  store.appendMessage(data, original.id, { role: 'user', text: '元の会話' });
  store.appendMessage(data, original.id, { role: 'assistant', text: '元の結果' });
  for (let i = 0; i < 51; i++) store.createSession(data, { repo, cli: 'claude' });
  const exported = path.join(root, 'export.json');
  fs.writeFileSync(exported, JSON.stringify({ sessionId: 'fixture-import-unique', customTitle: '外部の月次集計', workingDirectory: 'file:///C:/work/report', requests: [
    { requestId: 'r1', timestamp: Date.now(), message: { text: 'fixture-import-unique 集計を作成' }, response: [{ value: '集計しました' }], modelId: 'source-model', modelState: { value: 1 } },
    { requestId: 'r2', message: { text: 'この訂正は分岐後' }, response: [{ value: '後の結果' }], modelState: { value: 1 } },
  ] }));
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`], env: { ...process.env, KIRO_AGENTS_DIR: defs } });
  try {
    const win = await app.firstWindow(); win.setDefaultTimeout(30000);
    const errors = []; win.on('pageerror', e => errors.push(e.message));
    await win.waitForFunction(() => typeof document.getElementById('session-search-open')?.onclick === 'function');
    await win.evaluate(({ repo, id }) => openSessionInRepo(repo, id), { repo, id: original.id });
    await win.fill('#prompt', '書きかけを保持');
    await win.evaluate(() => { document.getElementById('search-source').value = 'app'; });
    await win.click('#session-search-open');
    await win.locator('#search-results button').first().waitFor();
    assert.equal(await win.locator('#search-results button').count(), 50);
    await win.click('#search-next');
    await win.waitForFunction(() => document.getElementById('search-status').textContent.startsWith('2ページ'));
    assert.equal(await win.locator('#search-results button').count(), 2);
    await win.click('#search-prev');
    await win.waitForFunction(() => document.getElementById('search-status').textContent.startsWith('1ページ'));
    assert.equal(await win.locator('#search-results button').count(), 50);
    await win.locator('#search-results button').first().click();
    await win.locator('#search-preview h3').waitFor();
    await win.fill('#search-text', '存在しない条件');
    assert.equal(await win.locator('#search-preview h3').count(), 0);
    assert.equal(await win.locator('#search-results .active').count(), 0);
    // An in-flight read must not restore a preview after filters change.
    await win.fill('#search-text', '');
    await win.locator('#search-results button').first().waitFor();
    await app.evaluate(({ ipcMain }) => {
      const original = ipcMain._invokeHandlers.get('sessions:read');
      ipcMain.removeHandler('sessions:read');
      ipcMain.handle('sessions:read', async (...args) => { await new Promise(resolve => setTimeout(resolve, 600)); return original(...args); });
    });
    await win.locator('#search-results button').first().click();
    await win.fill('#search-text', '条件を変更');
    await win.waitForFunction(() => !document.getElementById('search-status').textContent.includes('検索しています'));
    await win.waitForTimeout(800);
    assert.equal(await win.locator('#search-preview h3').count(), 0);
    assert.equal(await win.locator('#search-preview').textContent(), '会話を選ぶと内容を確認できます');
    await win.click('#session-search-close');
    assert.equal(await win.inputValue('#prompt'), '書きかけを保持');
    await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, exported);
    await win.evaluate(() => api.sessionBrowser.import(false));
    await win.evaluate(() => { document.getElementById('search-source').value = 'vscode'; document.getElementById('search-text').value = 'fixture-import-unique'; });
    await win.click('#session-search-open');
    await win.getByRole('button', { name: /外部の月次集計/ }).click();
    await win.getByRole('button', { name: 'ここからfork', exact: true }).first().waitFor();
    await win.screenshot({ path: '/tmp/agent-app-session-search.png' });
    await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.setMinimumSize(400, 400); w.setSize(520, 800); });
    await win.locator('#search-back').waitFor({ state: 'visible' });
    await win.screenshot({ path: '/tmp/agent-app-session-search-narrow.png', animations: 'disabled' });
    assert.equal(await win.locator('#search-results-pane').isVisible(), false);
    assert.equal(await win.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await win.click('#search-back');
    assert.equal(await win.locator('#search-results-pane').isVisible(), true);
    await win.getByRole('button', { name: /外部の月次集計/ }).click();
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 900));
    assert.doesNotMatch(await win.locator('#session-search').innerText(), /WSL|Windows/);
    await win.getByRole('button', { name: 'ここからfork', exact: true }).first().click();
    await win.selectOption('#search-target-repo', repo);
    await win.locator('#search-target-agent option[value="claude"]').waitFor({ state: 'attached' });
    await win.selectOption('#search-target-agent', 'claude');
    await win.fill('#search-target-model', 'target-model');
    await win.click('#search-transfer-start');
    await win.waitForFunction(() => !document.getElementById('search-summary').disabled);
    await win.fill('#search-summary', '編集した引き継ぎ内容');
    await win.fill('#search-request', '別の観点で確認する');
    await win.screenshot({ path: '/tmp/agent-app-session-transfer.png' });
    await app.evaluate(({ ipcMain }) => {
      ipcMain.removeHandler('turn:send');
      ipcMain.handle('turn:send', (_event, args) => { global.sentTransfer = args; return { ok: true, data: {} }; });
    });
    await win.click('#search-transfer-start');
    await win.waitForFunction(id => state.current?.id !== id && !document.getElementById('search-transfer-dialog').open && state.pending.size === 0, original.id);
    const sent = await app.evaluate(() => global.sentTransfer);
    assert.equal(sent.cli, 'claude'); assert.equal(sent.model, 'target-model');
    assert.match(sent.prompt, /編集した引き継ぎ内容/); assert.match(sent.prompt, /別の観点/);
    const created = store.readSession(data, sent.id);
    assert.match(await win.locator('#chat-origin').textContent(), /外部の月次集計/);
    assert.equal(created.externalOrigin.boundary, 'r1:assistant');
    assert.equal(created.externalOrigin.nativeId, 'fixture-import-unique');
    assert.deepEqual(created.cliSessions, {});
    assert.equal(store.readSession(data, original.id).messages.length, 2);
    assert.deepEqual(errors, []);
  } catch (err) { await (await app.firstWindow()).screenshot({ path: '/tmp/agent-app-session-search-failure.png' }); throw err; }
  finally { await app.close(); }
});
