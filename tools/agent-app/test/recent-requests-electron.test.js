'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const APP = path.resolve(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('最近の依頼: 別リポジトリの全種類を表示し会話を開く', async t => {
  const pw = playwright();
  if (!pw?._electron) return t.skip('Electron unavailable');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'recent-sidebar-'));
  const a = path.join(dir, 'repo-a'), b = path.join(dir, 'repo-b'), data = path.join(dir, 'userdata');
  fs.mkdirSync(a); fs.mkdirSync(b);
  const store = require('../src/main/store');
  store.saveConfig(data, { repos: [a, b], lastRepo: a, area: 'home', useWorktree: false, transport: 'headless', share: { enabled: false }, evaluation: { mode: 'off' } });
  let conversation;
  for (const [kind, title, day] of [['conversation', '天気の確認', '10'], ['task', '定期チェック', '12'], ['workflow', 'リリース手順', '11']]) {
    const s = store.createSession(data, { repo: b, cli: 'codex', kind, transport: 'headless', task: { machine: 'check' }, workflow: { id: 'release' } });
    s.title = title; s.updatedAt = '2026-09-' + day + 'T00:00:00.000Z';
    fs.writeFileSync(path.join(data, 'sessions', s.id + '.json'), JSON.stringify(s));
    if (kind === 'conversation') conversation = s.id;
  }
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', '--user-data-dir=' + data] });
  try {
    const win = await app.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('settings-open')?.onclick === 'function');
    await win.waitForFunction(() => document.querySelectorAll('#home-items .list-pick').length === 3);
    assert.equal(await win.textContent('#area-list-title'), '最近の依頼');
    assert.deepEqual(await win.locator('#home-items .sub').allTextContents(), ['タスク · repo-b', 'ワークフロー · repo-b', '会話 · repo-b']);
    assert.equal(await win.inputValue('#repo-select'), a);
    assert.equal(await win.locator('aside #usage-open').count(), 0);
    assert.equal(await win.locator('#chat-head #usage-open').isVisible(), true);
    assert.equal(await win.textContent('#usage-indicator'), '利用状況');
    for (const id of ['chat-views', 'changes-toggle', 'chat-more']) assert.equal(await win.locator('#' + id).isVisible(), false);
    await win.locator('#usage-open').click();
    await win.locator('#app-settings').waitFor({ state: 'visible' });
    await win.evaluate(() => $('app-settings').close());

    await win.locator('#home-items .list-pick').filter({ hasText: '天気の確認' }).click();
    await win.waitForFunction(id => state.current?.id === id && state.area === 'conversation', conversation);
    assert.equal(await win.inputValue('#repo-select'), b);
    assert.equal(await win.textContent('#area-list-title'), '会話');
    assert.equal(await win.locator('#home-items').isVisible(), false);
    assert.equal(await win.locator('#sessions').isVisible(), true);
    assert.equal(await win.locator('#sessions .list-pick').count(), 1);
    assert.match(await win.textContent('#sessions'), /天気の確認/);
    await win.selectOption('#repo-select', a);
    await win.waitForFunction(() => state.sessions.length === 0);
    assert.equal(await win.locator('#sessions .list-pick').count(), 0);
    await win.selectOption('#repo-select', b);
    await win.waitForFunction(() => document.querySelectorAll('#sessions .list-pick').length === 1);
    assert.equal(await win.locator('#usage-open').isVisible(), false);
    for (const id of ['chat-views', 'changes-toggle', 'chat-more']) assert.equal(await win.locator('#' + id).isVisible(), true);
    for (const [area, label] of [['tasks', 'タスク'], ['workflows', 'ワークフロー']]) {
      await win.evaluate(async area => { await showArea(area); }, area);
      assert.equal(await win.textContent('#area-list-title'), label);
      assert.equal(await win.locator('#' + area).isVisible(), true);
      assert.equal(await win.locator('#home-items').isVisible(), false);
      assert.equal(await win.inputValue('#repo-select'), b);
    }
    await win.evaluate(async () => { showView('files'); await showArea('home'); });
    assert.equal(await win.locator('#chat').isVisible(), true);
    assert.equal(await win.locator('#files').isVisible(), false);

    assert.equal(await win.textContent('#area-list-title'), '最近の依頼');
    assert.equal(await win.locator('#home-items').isVisible(), true);
    assert.equal(await win.locator('#home-items .list-pick').count(), 3);
  } finally { await app.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});
