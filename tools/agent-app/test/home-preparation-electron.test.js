'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const APP = path.resolve(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('home: preparation appears before readiness, updates during selection, and clears on success/error', async t => {
  const pw = playwright();
  if (!pw?._electron) return t.skip('Electron unavailable');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'home-preparation-'));
  const repo = path.join(dir, 'repo'), data = path.join(dir, 'userdata');
  fs.mkdirSync(repo);
  const store = require('../src/main/store');
  store.saveConfig(data, { repos: [repo], lastRepo: repo, area: 'home', allocation: { mode: 'auto' }, useWorktree: false, transport: 'headless', share: { enabled: false }, evaluation: { mode: 'off' } });
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', `--user-data-dir=${data}`] });
  try {
    const win = await app.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function' && !state.agentsLoading);
    await app.evaluate(({ ipcMain, app }) => {
      const req = process.getBuiltinModule('module').createRequire(`${app.getAppPath()}/package.json`);
      const store = req('./src/main/store');
      const replace = (name, fn) => { ipcMain.removeHandler(name); ipcMain.handle(name, fn); };
      global.sendCount = 0;
      replace('turn:send', async (event, p) => {
        global.sendCount++;
        global.preparationEvent = event; global.preparationId = p.id;
        event.sender.send('turn:progress', { id: p.id, item: { text: '判定中…\n進め方を判定しています。', status: 'running', preparing: true } });
        const fail = await new Promise(resolve => { global.finishPreparation = resolve; });
        if (fail) return { ok: false, error: '判定に失敗しました' };
        store.updateSession(app.getPath('userData'), p.id, { transport: 'tmux' });
        return { ok: true, data: { acceptedAt: new Date().toISOString() } };
      });
      replace('term:open', () => ({ ok: true, data: { phase: 'ready', name: 'test-terminal' } }));
      replace('term:watch', () => ({ ok: true, data: true }));
      replace('term:resize', () => ({ ok: true, data: true }));
    });
    await win.evaluate(async () => {
      await showArea('home');
      state.agents = [{ name: 'codex', available: true, interactive: true }];
      state.agentsReady = new Promise(resolve => { window.releaseReady = resolve; });
      $('prompt').value = '今日の天気は？';
      window.layout = () => Object.fromEntries(['main', 'chat', 'composer', 'prompt', 'run-settings', 'home-repository-slot'].map(id => {
        const r = $(id).getBoundingClientRect(); return [id, { x: r.x, y: r.y, width: r.width, height: r.height }];
      }));
      window.beforeLayout = layout();
      window.sendFinished = false;
      window.submission = sendPrompt().finally(() => { window.sendFinished = true; });
    });
    await win.locator('#turn-preparation:not([hidden])').waitFor();
    assert.deepEqual(await win.evaluate(() => layout()), await win.evaluate(() => beforeLayout));
    assert.match(await win.textContent('#turn-preparation'), /実行環境/);
    assert.equal(await win.locator('#terminal-stage').isVisible(), true);
    assert.equal(await win.locator('#conversation-start').isVisible(), false);
    assert.equal(await win.locator('#send').isDisabled(), true);
    assert.deepEqual(await win.locator('#turn-preparation').evaluate(n => ({ bg: getComputedStyle(n).backgroundColor, fg: getComputedStyle(n).color })), { bg: 'rgb(11, 15, 20)', fg: 'rgb(216, 222, 233)' });
    await win.evaluate(() => { releaseReady(); });
    await win.waitForFunction(() => $('turn-preparation').textContent.includes('進め方'));
    await app.evaluate(() => {
      preparationEvent.sender.send('turn:progress', { id: 'another-session', item: { text: '別の会話', preparing: true } });
      preparationEvent.sender.send('turn:progress', { id: preparationId, item: { text: '判定中…\nエージェントとモデルを選択しています。', preparing: true } });
    });
    await win.waitForFunction(() => $('turn-preparation').textContent.includes('モデルを選択'));
    assert.deepEqual(await win.evaluate(() => layout()), await win.evaluate(() => beforeLayout));
    for (const width of [1024, 768, 375]) {
      await win.setViewportSize({ width, height: 821 });
      const pair = await win.evaluate(() => {
        const preparation = state.preparation;
        const current = state.current;
        const pending = new Set(state.pending);
        const running = new Set(state.running);
        state.preparation = null; state.current = null; state.pending.clear(); state.running.clear();
        renderHeader();
        const before = layout();
        state.preparation = preparation; state.current = current; state.pending = pending; state.running = running;
        renderHeader();
        return { before, after: layout(), overflow: $('composer').scrollWidth > $('composer').clientWidth };
      });
      assert.deepEqual(pair.after, pair.before, 'layout stable at ' + width);
      assert.equal(pair.overflow, false, 'no horizontal overflow at ' + width);
    }
    await win.setViewportSize({ width: 1360, height: 821 });
    await win.screenshot({ path: '/tmp/agent-app-home-preparation.png' });
    await app.evaluate(() => finishPreparation(false));
    await win.waitForFunction(() => window.sendFinished && state.area === 'conversation');
    assert.equal(await win.locator('#turn-preparation').isVisible(), false);
    assert.equal(await win.locator('#term-host').isVisible(), true);
    assert.equal(await win.inputValue('#prompt'), '');

    await win.evaluate(async () => {
      await showArea('home');
      $('prompt').value = '失敗しても残す依頼';
      window.sendFinished = false;
      window.submission = sendPrompt().finally(() => { window.sendFinished = true; });
    });
    await win.waitForFunction(() => $('turn-preparation').textContent.includes('進め方'));
    await app.evaluate(() => finishPreparation(true));
    await win.waitForFunction(() => window.sendFinished);
    assert.equal(await win.locator('#turn-preparation').isVisible(), false);
    assert.equal(await win.inputValue('#prompt'), '失敗しても残す依頼');
    assert.match(await win.textContent('#notice'), /判定に失敗/);
    assert.equal(await win.locator('#send').isEnabled(), true);
    assert.equal(await app.evaluate(() => sendCount), 2);
  } finally { await app.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
