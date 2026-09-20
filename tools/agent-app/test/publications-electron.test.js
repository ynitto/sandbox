'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), os = require('os');
const store = require('../src/main/store');
const { Share } = require('../src/main/share');
const settings = require('../src/main/settings');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}
test('Electron: publish after completion, shared comments, explicit search, common fork and revoke', async t => {
  const pw = playwright(); if (!pw?._electron) return t.skip('Playwright unavailable');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'publications-ui-'));
  const data = path.join(root, 'data'), remoteData = path.join(root, 'remote'), repo = path.join(root, 'repo'), defs = path.join(root, 'agents');
  fs.mkdirSync(repo); fs.mkdirSync(defs);
  const remote = new Share({ userData: remoteData, config: settings.normalize({ share: { enabled: true, node: 'bob', passphrase: 'ui-test', accept: 'manual' } }),
    options: { udp: false, port: 0, host: '127.0.0.1' }, screen: async () => '> 完了済みの端末' });
  await remote.start(); assert.equal(remote.state, 'on', remote.error);
  const original = store.createSession(remoteData, { repo: '/remote/repo', cli: 'claude', model: 'remote-model' });
  store.appendMessage(remoteData, original.id, { role: 'user', text: '共有fixture集計' });
  store.appendMessage(remoteData, original.id, { role: 'assistant', text: '集計の結果です' });
  const entry = remote.publish(original.id);
  let remoteSearches = 0;
  const search = remote.publications.search.bind(remote.publications);
  remote.publications.search = (...args) => { remoteSearches++; return search(...args); };
  const script = path.join(root, 'summary.py');
  fs.writeFileSync(script, 'import sys\nsys.stdin.read()\nprint("目的: 集計。結果を確認する。")\n');
  const spec = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../agents/claude.json')));
  spec.command = ['python3', script]; fs.writeFileSync(path.join(defs, 'claude.json'), JSON.stringify(spec));
  store.saveConfig(data, { repos: [repo], lastRepo: repo, transport: 'headless', useWorktree: false,
    share: { enabled: true, node: 'alice', passphrase: 'ui-test', accept: 'manual', udp: false, port: 0, peers: [`127.0.0.1:${remote.port}`] } });
  const local = store.createSession(data, { repo, cli: 'claude', transport: 'headless' });
  store.appendMessage(data, local.id, { role: 'user', text: '自分の作業' });
  store.appendMessage(data, local.id, { role: 'assistant', text: '自分の結果' });
  const app = await pw._electron.launch({ executablePath: require('electron'), args: [path.resolve(__dirname, '..'), '--no-sandbox', `--user-data-dir=${data}`], env: { ...process.env, KIRO_AGENTS_DIR: defs } });
  try {
    const win = await app.firstWindow(); win.setDefaultTimeout(20000);
    const errors = []; win.on('pageerror', e => errors.push(e.message));
    await win.waitForFunction(() => typeof document.getElementById('session-publish')?.onclick === 'function');
    await win.waitForFunction(async () => (await api.share.status()).peers.some(p => p.node === 'bob'));
    await win.evaluate(({ repo, id }) => openSessionInRepo(repo, id), { repo, id: local.id });
    await win.click('#chat-more > summary'); await win.click('#session-publish');
    await win.waitForFunction(async () => (await api.share.status()).publications.length === 1);
    await win.click('#area-share');
    await win.locator('#share-requests button').filter({ hasText: '共有fixture集計' }).click();
    await win.locator('#share-cards').getByText('集計の結果です', { exact: true }).waitFor();
    await win.locator('#share-terminal').waitFor({ state: 'visible' });
    await win.fill('#share-prompt', 'コメントfixture'); await win.click('#share-send');
    await win.locator('#share-thread-body').getByText('コメントfixture', { exact: true }).waitFor({ state: 'attached' });
    assert.equal((await remote.publications.view(entry.id)).talk[0].who, 'alice');
    await win.screenshot({ path: '/tmp/agent-app-public-session.png' });
    // Local typing must never fan out to peers.
    await win.click('#session-search-open');
    await win.evaluate(() => { document.getElementById('search-source').value = 'app'; });
    const before = remoteSearches;
    await win.fill('#search-text', '共有fixture');
    await win.waitForFunction(() => /^該当なし（|^該当する会話なし|^該当する会話はありません/.test(document.getElementById('search-status').textContent));
    assert.equal(remoteSearches, before);
    await win.click('#search-more > summary');
    await win.click('#search-shared');
    await win.click('#search-more > summary');
    await win.locator('#search-results button').filter({ hasText: '共有fixture集計' }).click();
    await win.locator('#search-preview').getByText('集計の結果です', { exact: true }).waitFor();
    assert.ok(remoteSearches > before);
    assert.match(await win.locator('#search-preview').innerText(), /bob（共有）/);
    await win.screenshot({ path: '/tmp/agent-app-public-search.png' });
    await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.setMinimumSize(400, 400); w.setSize(520, 800); });
    assert.equal(await win.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await win.screenshot({ path: '/tmp/agent-app-public-search-narrow.png' });
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 900));
    await win.getByRole('button', { name: 'フォーク', exact: true }).first().click();
    await win.selectOption('#search-target-repo', repo);
    await win.locator('#search-target-agent option[value="claude"]').waitFor({ state: 'attached' });
    assert.equal(await win.inputValue('#search-boundary'), '1');
    await app.evaluate(({ ipcMain }) => {
      ipcMain.removeHandler('turn:send');
      ipcMain.handle('turn:send', (_event, args) => { global.publicFork = args; return { ok: true, data: {} }; });
    });
    await win.fill('#search-request', '続きの作業'); await win.click('#search-transfer-start');
    await win.waitForFunction(() => !document.getElementById('search-transfer-dialog').open);
    const fork = await app.evaluate(() => global.publicFork);
    assert.ok(fork); assert.match(fork.prompt, /続きの作業/);
    const forked = store.readSession(data, fork.id);
    assert.equal(forked.externalOrigin.key, `public:bob:${entry.id}`);
    assert.equal(forked.repo, repo);
    await win.evaluate(({ repo, id }) => openSessionInRepo(repo, id), { repo, id: local.id });
    await win.click('#chat-more > summary');
    assert.equal(await win.locator('#session-publish').textContent(), '公開を停止');
    await win.click('#session-publish');
    await win.waitForFunction(async () => (await api.share.status()).publications.length === 0);
    assert.deepEqual(errors, []);
  } finally { await app.close(); await remote.stop(); fs.rmSync(root, { recursive: true, force: true }); }
});
