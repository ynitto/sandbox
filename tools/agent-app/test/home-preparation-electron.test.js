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
        store.updateSession(app.getPath('userData'), p.id, { transport: 'tmux', cli: 'codex', model: 'test-model', modelSelection: { cli: 'codex', model: 'test-model', stage: 'audit' } });
        store.appendMessage(app.getPath('userData'), p.id, { role: 'user', text: p.text });
        store.appendMessage(app.getPath('userData'), p.id, { role: 'assistant', text: 'どこの天気ですか？' });
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
    for (const width of [1360, 1024, 900, 821, 768, 375]) {
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
      const toolbar = await win.evaluate(() => {
        const ids = ['attach', 'stop', 'send'];
        const rects = ids.map(id => $(id).getBoundingClientRect());
        return {
          centers: rects.map(r => r.y + r.height / 2),
          heights: rects.map(r => r.height),
          widths: rects.map(r => r.width),
        };
      });
      assert.ok(Math.max(...toolbar.centers) - Math.min(...toolbar.centers) < 2, 'toolbar stays on one row at ' + width);
      assert.ok(Math.max(...toolbar.heights) <= 40, 'toolbar labels stay on one line at ' + width);
      assert.ok(toolbar.widths.every(w => w > 0), 'all toolbar controls remain visible at ' + width);
      const context = await win.evaluate(() => {
        const shell = document.querySelector('#composer .composer-shell').getBoundingClientRect();
        const controls = ['run-settings', 'home-repository-slot'].map(id => {
          const element = $(id), r = element.getBoundingClientRect();
          return { outside: !element.closest('.composer-shell'), x: r.x, y: r.y, width: r.width };
        });
        return { left: shell.left, bottom: shell.bottom, controls };
      });
      assert.ok(context.controls.every(c => c.outside && c.y >= context.bottom && c.width > 0), 'selectors below input border at ' + width);
      assert.ok(Math.abs(context.controls[0].x - context.left) <= 1, 'selectors aligned left at ' + width);

    }
    await win.setViewportSize({ width: 1360, height: 821 });
    await win.screenshot({ path: '/tmp/agent-app-home-preparation.png' });

    // 起動先が tmux に決まったら、依頼が CLI に届くのを待たずに端末ミラーを出す。
    assert.equal(await win.evaluate(() => Term.current()), '', '起動先が決まるまでは端末を出さない');
    await app.evaluate(() => preparationEvent.sender.send('turn:transport', { id: preparationId, transport: 'tmux' }));
    await win.waitForFunction(() => Term.current() === state.current.id);
    assert.equal(await win.evaluate(() => window.sendFinished), false, 'ターンはまだ終わっていない');
    assert.equal(await win.locator('#term-host').isVisible(), true);
    assert.equal(await win.locator('#turn-preparation').isVisible(), false, '端末が出たら黒い面は端末に渡す');
    assert.match(await win.textContent('#term-agent'), /準備中/, '準備中はこの面の見出しに 1 行で残す');

    await app.evaluate(() => finishPreparation(false));
    await win.waitForFunction(() => window.sendFinished && state.area === 'conversation');
    assert.equal(await win.locator('#turn-preparation').isVisible(), false);
    assert.equal(await win.locator('#term-host').isVisible(), true);
    assert.equal(await win.inputValue('#prompt'), '');

    // 応答完了後の返信でも、同じ会話と選択済みモデルを維持し準備画面を出さない。
    const firstId = await win.evaluate(() => state.current.id);
    await win.evaluate(() => {
      state.running.clear();
      $('prompt').value = '東京';
      window.sendFinished = false;
      window.submission = sendPrompt().finally(() => { window.sendFinished = true; });
    });
    await app.evaluate(async () => {
      const deadline = Date.now() + 5000;
      while (sendCount !== 2 && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
      if (sendCount !== 2) throw new Error('返信が送信処理へ届きませんでした');
    });
    assert.equal(await win.evaluate(() => state.preparation), null);
    assert.equal(await win.locator('#turn-preparation').isVisible(), false);
    assert.equal(await win.locator('#conversation-history').isVisible(), true);
    assert.equal(await win.evaluate(() => state.current.id), firstId);
    assert.equal(await win.evaluate(() => selectedExecution().allocation || ''), '');
    await app.evaluate(() => finishPreparation(false));
    await win.waitForFunction(() => window.sendFinished);
    assert.equal(await win.evaluate(() => state.current.messages.length), 4);

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
    assert.equal(await app.evaluate(() => sendCount), 3);
  } finally { await app.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
