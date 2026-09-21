'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const APP = path.resolve(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}
test('reuse UI: classify, edit, create fresh sessions for all three kinds in a selected repository, edit execution settings, reuse inputs and open artifacts', async t => {
  const pw = playwright(); let binary;
  try { binary = require('electron'); } catch {}
  if (!pw?._electron || !binary || (process.platform === 'linux' && !process.env.DISPLAY)) return t.skip('Electron display unavailable');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reuse-ui-'));
  const repo = path.join(dir, 'repo'), data = path.join(dir, 'userdata');
  fs.mkdirSync(repo);
  const target = path.join(dir, 'target'); fs.mkdirSync(target);
  const added = path.join(dir, 'added'); fs.mkdirSync(added);
  const store = require('../src/main/store');
  store.saveConfig(data, { repos: [repo, target], lastRepo: repo, area: 'conversation', useWorktree: false, transport: 'headless' });
  const session = store.createSession(data, { repo, cli: 'codex', transport: 'headless', readonly: true });
  store.appendMessage(data, session.id, { role: 'user', text: '月次集計を作成' });
  store.appendMessage(data, session.id, { role: 'assistant', text: '集計しました。\n@artifact reports/month.xlsx' });
  const machine = { id: 'machine:report', kind: 'statemachine', machine: 'report', name: '月次集計', parameters: ['month'], history: [{ runId: 'previous', ok: true, agentCli: 'codex', model: 'test-model', parameters: { month: '2026-08' }, finishedAt: '2026-09-13' }] };
  require('../src/main/automation/store').save(repo, { name: '月次集計', machine: 'report', purpose: '集計', steps: [{ kind: 'agent', title: '集計', detail: '{{month}}を集計' }] });
  const electron = await pw._electron.launch({ executablePath: binary, args: [APP, '--no-sandbox', `--user-data-dir=${data}`] });
  try {
    const win = await electron.firstWindow();
    win.setDefaultTimeout(15000);
    const errors = []; win.on('pageerror', e => errors.push(e.message));
    await win.waitForFunction(() => typeof document.getElementById('session-routine')?.onclick === 'function');
    await electron.evaluate(({ ipcMain }, machine) => {
      const replace = (name, fn) => { ipcMain.removeHandler(name); ipcMain.handle(name, fn); };
      replace('automation:capabilities', () => ({ ok: true, data: { agentFlow: true, agentLoop: true, herd: false } }));
      replace('automation:run:snapshot', () => ({ ok: true, data: { available: true, tasks: [machine], daemon: { running: false } } }));
      replace('automation:ai:start', (event, payload) => {
        if (payload.mode !== 'routine' || !payload.request.includes('月次集計')) throw new Error('missing source');
        setTimeout(() => event.sender.send('automation:ai:result', { requestId: 'routine-test', mode: 'routine', ok: true, result: { kind: 'task', reason: '1つの担当が順に実行するため', purpose: '確定した月次集計の手順' } }), 20);
        return { ok: true, data: { requestId: 'routine-test' } };
      });
      replace('turn:send', (_event, args) => { global.lastCreation = args; return { ok: true, data: {} }; });
      replace('shell:openFile', (_event, args) => { global.lastArtifact = args; return { ok: true, data: '' }; });
    }, machine);
    await win.reload();
    await win.waitForFunction(() => typeof document.getElementById('session-routine')?.onclick === 'function');
    await win.locator('#sessions .row-item').first().click();
    await win.getByRole('button', { name: 'reports/month.xlsx', exact: true }).click();
    assert.equal((await electron.evaluate(() => global.lastArtifact)).rel, 'reports/month.xlsx');
    const createdIds = new Set();
    for (const kind of ['task', 'workflow', 'skill']) {
      await win.evaluate(async ({ repo, id }) => { await openSessionInRepo(repo, id); }, { repo, id: session.id });
      await win.locator('#chat-more summary').click(); await win.click('#session-routine');
      await win.locator('#routine-create:not([disabled])').waitFor();
      assert.equal(await win.inputValue('#routine-repo'), '');
      await win.click('#routine-create');
      await win.getByText('保存先のリポジトリ（フォルダ）を選んでください', { exact: true }).waitFor();
      await win.selectOption('#routine-kind', kind);
      await win.selectOption('#routine-repo', kind === 'task' ? repo : target);
      if (kind === 'skill') {
        await electron.evaluate(({ dialog }) => { dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] }); });
        await win.click('#routine-add-repo');
        assert.equal(await win.inputValue('#routine-repo'), target);
        await electron.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, added);
        await win.click('#routine-add-repo');
        await win.waitForFunction(folder => document.getElementById('routine-repo').value === folder, added);
      }
      await win.fill('#routine-purpose', '訂正後の月次集計手順');
      if (kind === 'skill') await win.screenshot({ path: '/tmp/agent-app-routine.png' });
      await win.click('#routine-create');
      await win.waitForFunction(() => !document.getElementById('routine-dialog').open);
      await win.waitForFunction(() => state.current?.messages !== undefined && document.getElementById('routine-create').disabled === false);
      const sent = await electron.evaluate(() => global.lastCreation);
      assert.notEqual(sent.id, session.id);
      assert.equal(createdIds.has(sent.id), false); createdIds.add(sent.id);
      assert.match(sent.prompt, /訂正後の月次集計手順/);
      assert.equal(sent.readonly, false);
      assert.equal(sent.autoApprove, false);
      const created = store.readSession(data, sent.id);
      assert.equal(created.repo, kind === 'task' ? repo : kind === 'skill' ? added : target);
      assert.equal(created.origin.sessionId, session.id);
      assert.deepEqual(created.cliSessions, {});
      assert.equal(store.readSession(data, session.id).messages.length, 2);
    }
    await win.evaluate(async ({ repo, id }) => { await openSessionInRepo(repo, id); }, { repo, id: session.id });
    await win.locator('#run-settings summary').click();
    assert.equal(await win.locator('#run-preset, #preset-save, #preset-delete').count(), 0);
    await win.selectOption('#permission-mode', 'ask');
    await win.locator('#run-settings summary').click();
    assert.match(await win.locator('#run-settings-summary').textContent(), /読み取り専用/);
    await win.locator('#run-settings summary').click();
    await win.fill('#model', 'long-model-name-for-layout-verification-2026');
    await win.locator('#run-settings summary').click();
    for (const width of [1280, 768, 375]) {
      await win.setViewportSize({ width, height: 900 });
      await win.waitForTimeout(250); // サイドバーの幅変更アニメーションが終わってから測る
      const label = win.locator(width <= 640 ? '.run-settings-compact' : '#run-settings-summary');
      const dimensions = await label.evaluate(node => ({
        client: node.clientWidth, scroll: node.scrollWidth,
        left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right,
      }));
      assert.ok(dimensions.client >= dimensions.scroll, `設定が文字切れしている: ${width}`);
      assert.ok(dimensions.left >= 0 && dimensions.right <= width, `設定が画面外に出ている: ${width}`);
      const trigger = await win.locator('#run-settings > summary').boundingBox();
      const prompt = await win.locator('#prompt').boundingBox();
      if (width === 375) await win.screenshot({ path: '/tmp/agent-app-settings-narrow.png' });
      assert.ok(trigger.y >= prompt.y + prompt.height && trigger.y + trigger.height <= 900, `設定が入力欄と重なる: ${width} ${JSON.stringify({ trigger, prompt })}`);
    }
    await win.screenshot({ path: '/tmp/agent-app-settings-narrow.png' });
    await win.locator('#run-settings summary').click();
    await win.fill('#model', '');
    await win.locator('#run-settings summary').click();
    await win.setViewportSize({ width: 1280, height: 900 });
    await win.screenshot({ path: '/tmp/agent-app-settings-summary.png' });
    await win.locator('#run-settings summary').click();
    await win.screenshot({ path: '/tmp/agent-app-settings-popover.png' });
    await win.click('#area-tasks');
    await win.locator('#tasks .row-item').filter({ hasText: '月次集計' }).click();
    const panel = win.locator('#automation-workbench');
    await panel.locator('[data-task-tab="history"]').click(); await panel.locator('[data-history-reuse]').click();
    assert.equal(await panel.locator('[data-run-param="month"]').inputValue(), '2026-08');
    await panel.locator('[data-date-mode="run"]').selectOption('@date:previous-month');
    assert.equal(await panel.locator('[data-run-param="month"]').getAttribute('readonly'), '');
    await win.screenshot({ path: '/tmp/agent-app-reuse.png' });
    assert.deepEqual(errors, []);
  } catch (err) { const win = await electron.firstWindow(); await win.screenshot({ path: '/tmp/agent-app-reuse-failure.png' }); throw err; } finally { await electron.close(); }
});
