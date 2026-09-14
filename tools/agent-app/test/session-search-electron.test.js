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
  const methodFile = path.join(root, 'method.txt'); fs.writeFileSync(methodFile, 'skill');
  fs.writeFileSync(summary, `import sys,json\nfrom pathlib import Path\nprompt=sys.stdin.read()\nkind=Path(${JSON.stringify(methodFile)}).read_text()\nprint(json.dumps({"kind":kind,"reason":"再利用対象に適するため","purpose":"毎月の集計方法を作成する"},ensure_ascii=False) if 'JSONだけを返す:' in prompt else "目的: 月次集計。次の作業: 結果を確認する。")\n`);
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
    await win.locator('#search-results').getByRole('button', { name: /外部の月次集計/ }).click();
    await win.getByRole('button', { name: 'フォーク', exact: true }).first().waitFor();
    await win.screenshot({ path: '/tmp/agent-app-session-search.png' });
    await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.setMinimumSize(400, 400); w.setSize(520, 800); });
    await win.locator('#search-back').waitFor({ state: 'visible' });
    await win.screenshot({ path: '/tmp/agent-app-session-search-narrow.png', animations: 'disabled' });
    assert.equal(await win.locator('#search-results-pane').isVisible(), false);
    assert.equal(await win.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await win.click('#search-back');
    assert.equal(await win.locator('#search-results-pane').isVisible(), true);
    await win.locator('#search-results').getByRole('button', { name: /外部の月次集計/ }).click();
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 900));
    assert.doesNotMatch(await win.locator('#session-search').innerText(), /WSL|Windows/);
    await win.getByRole('button', { name: 'フォーク', exact: true }).first().click();
    await win.selectOption('#search-target-repo', repo);
    await win.locator('#search-target-agent option[value="claude"]').waitFor({ state: 'attached' });
    await win.click('#search-execution-settings > summary');
    await win.selectOption('#search-target-agent', 'claude');
    await win.fill('#search-target-model', 'target-model');
    await win.click('#search-execution-settings > summary');
    assert.equal(await win.inputValue('#search-boundary'), 'r1:assistant');
    await win.fill('#search-request', '別の観点で確認する');
    assert.equal(await win.locator('#search-transfer-start').evaluate(b => b.getBoundingClientRect().right > b.parentElement.getBoundingClientRect().left + b.parentElement.clientWidth * 0.8), true);
    await win.screenshot({ path: '/tmp/agent-app-session-transfer.png' });
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(520, 800));
    assert.equal(await win.evaluate(() => { const body = document.querySelector('#search-transfer-dialog .dlg-body'); return body.scrollWidth <= body.clientWidth; }), true);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 900));
    await app.evaluate(({ ipcMain }) => {
      ipcMain.removeHandler('turn:send');
      ipcMain.handle('turn:send', async (event, args) => {
        global.sentTransfer = args;
        const held = new Promise(resolve => { global.releaseImportStart = resolve; });
        event.sender.send('turn:started', { id: args.id });
        await held;
        return { ok: true, data: {} };
      });
    });
    await win.click('#search-transfer-start');
    await win.waitForFunction(id => state.current?.id !== id && !document.getElementById('search-transfer-dialog').open, original.id);
    assert.equal(await app.evaluate(() => !!global.releaseImportStart), true);
    await app.evaluate(() => { global.releaseImportStart(); global.releaseImportStart = null; });
    await win.waitForFunction(() => state.pending.size === 0);
    const sent = await app.evaluate(() => global.sentTransfer);
    assert.equal(sent.cli, 'claude'); assert.equal(sent.model, 'target-model');
    assert.match(sent.prompt, /月次集計/); assert.match(sent.prompt, /別の観点/);
    const created = store.readSession(data, sent.id);
    assert.match(await win.locator('#chat-origin').textContent(), /外部の月次集計/);
    assert.equal(created.externalOrigin.boundary, 'r1:assistant');
    assert.equal(created.externalOrigin.nativeId, 'fixture-import-unique');
    assert.deepEqual(created.cliSessions, {});
    assert.equal(store.readSession(data, original.id).messages.length, 2);
    assert.match(store.readSession(data, sent.id).title, /（フォーク）/);
    // 会話画面からも、同じダイアログで開いている会話をフォークできる
    await win.evaluate(({ repo, id }) => openSessionInRepo(repo, id), { repo, id: original.id });
    // 応答の下の「フォーク」も同じダイアログを、その応答の位置で開く
    await win.getByRole('button', { name: 'フォーク', exact: true }).first().click();
    await win.locator('#search-transfer-dialog[open]').waitFor();
    assert.equal(await win.inputValue('#search-boundary'), '1');
    assert.equal(await win.inputValue('#search-intent'), 'session');
    await win.click('#search-transfer-close');
    await win.waitForFunction(() => !document.getElementById('search-transfer-dialog').open);
    // 定型の依頼は入力欄の「定型」から入れる（会話の履歴には出さない）
    assert.equal(await win.locator('#quick-menu').evaluate(m => m.hidden), false);
    await win.locator('#quick-menu summary').click();
    const quick = win.locator('#quick-menu-list button');
    assert.ok(await quick.count() > 0);
    const label = await quick.first().textContent();
    await win.screenshot({ path: '/tmp/agent-app-conversation-actions.png' });
    await quick.first().click();
    await win.waitForFunction(() => !document.getElementById('quick-menu').open);
    assert.ok((await win.inputValue('#prompt')).length > 0, label + ' の本文が入力欄に入る');
    await win.fill('#prompt', '');
    await win.locator('#chat-more summary').click();
    await win.click('#session-fork');
    await win.locator('#search-transfer-dialog[open]').waitFor();
    assert.equal(await win.locator('#search-boundary option').count(), 1);
    assert.equal(await win.inputValue('#search-target-repo'), repo);
    await win.locator('#search-target-agent option[value="claude"]').waitFor({ state: 'attached' });
    await win.screenshot({ path: '/tmp/agent-app-conversation-fork.png' });
    await win.click('#search-transfer-start');
    await win.waitForFunction(id => state.current?.id !== id && !document.getElementById('search-transfer-dialog').open, original.id);
    await app.evaluate(() => { global.releaseImportStart(); global.releaseImportStart = null; });
    await win.waitForFunction(() => state.pending.size === 0);
    const forked = await app.evaluate(() => global.sentTransfer);
    assert.match(forked.prompt, /元の会話:/);
    assert.notEqual(forked.id, sent.id);
    assert.equal(store.readSession(data, forked.id).origin.sessionId, original.id);
    assert.equal(store.readSession(data, original.id).messages.length, 2);
    await app.evaluate(({ ipcMain }) => {
      global.methodStarts = [];
      global.releaseImportTerminal = null;
      ipcMain.removeHandler('term:open');
      ipcMain.handle('term:open', async () => {
        await new Promise(resolve => { global.releaseImportTerminal = resolve; });
        return { ok: true, data: { phase: 'ready' } };
      });
      ipcMain.removeHandler('term:watch');
      ipcMain.handle('term:watch', () => ({ ok: true, data: {} }));
      for (const prefix of ['automation:teach', 'automation:flow:teach']) {
        const read = ipcMain._invokeHandlers.get(prefix + ':session');
        ipcMain.removeHandler(prefix + ':start');
        ipcMain.handle(prefix + ':start', async (event, args) => {
          global.methodStarts.push({ prefix, ...args });
          const view = await read(event, args);
          const held = new Promise(resolve => { global.releaseImportStart = resolve; });
          event.sender.send('turn:started', { id: view.data.session.id });
          await held;
          return view;
        });
      }
    });
    for (const kind of ['task', 'workflow', 'skill']) {
      fs.writeFileSync(methodFile, kind);
      await win.click('#session-search-open');
      await win.locator('#search-results').getByRole('button', { name: /外部の月次集計/ }).click();
      await win.getByRole('button', { name: 'フォーク', exact: true }).first().click();
      await win.selectOption('#search-target-repo', repo);
      await win.locator('#search-target-agent option[value="claude"]').waitFor({ state: 'attached' });
      await win.click('#search-execution-settings > summary');
      await win.selectOption('#search-target-agent', 'claude');
      await win.fill('#search-target-model', kind + '-model');
      await win.click('#search-execution-settings > summary');
      // フォーク先を選ぶだけで、選んだ後の遷移はこれまでと同じ
      await win.selectOption('#search-intent', kind);
      if (kind === 'workflow') await win.screenshot({ path: '/tmp/agent-app-session-transfer-kind.png' });
      await win.click('#search-transfer-start');
      if (kind !== 'skill') {
        await win.waitForFunction(() => document.getElementById('search-transfer-status').textContent.includes('表示を準備'));
        for (let attempt = 0; attempt < 100; attempt++) {
          if (await app.evaluate(() => !!global.releaseImportTerminal)) break;
          await win.waitForTimeout(50);
        }
        assert.equal(await app.evaluate(() => !!global.releaseImportTerminal), true);
        assert.equal(await win.locator('#search-transfer-dialog').evaluate(d => d.open), true);
        await app.evaluate(() => { global.releaseImportTerminal(); global.releaseImportTerminal = null; });
      }
      await win.waitForFunction(() => !document.getElementById('search-transfer-dialog').open);
      assert.equal(await app.evaluate(() => !!global.releaseImportStart), true);
      await app.evaluate(() => { global.releaseImportStart(); global.releaseImportStart = null; });
      if (kind === 'skill') {
        const skillSent = await app.evaluate(() => global.sentTransfer);
        assert.match(skillSent.prompt, /SKILL.md/); assert.equal(skillSent.model, 'skill-model');
        assert.equal(await win.evaluate(() => state.area), 'conversation');
      } else {
        await win.waitForFunction(kind => kind === 'task' ? TaskTeaching.state.visible && !TaskTeaching.state.pending : FlowTeaching.state.visible && !FlowTeaching.state.pending, kind);
        const calls = await app.evaluate(() => global.methodStarts);
        assert.ok(calls.some(c => c.model === kind + '-model' && c.cli === 'claude' && c.repo === repo && c.policy === 'direct'));
        assert.equal(await win.evaluate(() => state.area), kind === 'task' ? 'tasks' : 'workflows');
      }
    }
    assert.deepEqual(errors, []);
  } catch (err) { console.error(await (await app.firstWindow()).evaluate(() => ({ status: document.getElementById('search-transfer-status').textContent, area: state.area, task: { visible: TaskTeaching.state.visible, pending: TaskTeaching.state.pending }, flow: { visible: FlowTeaching.state.visible, pending: FlowTeaching.state.pending } }))); await (await app.firstWindow()).screenshot({ path: '/tmp/agent-app-session-search-failure.png' }); throw err; }
  finally { await app.close(); }
});
