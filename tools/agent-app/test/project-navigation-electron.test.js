'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const APP = path.resolve(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('project navigation: selection, cross-repository sessions, empty state, and restoration', async t => {
  const pw = playwright();
  if (!pw?._electron) return t.skip('Electron unavailable');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'project-navigation-'));
  const data = path.join(dir, 'userdata'), kb = path.join(dir, 'kb');
  const first = path.join(dir, 'first'), second = path.join(dir, 'second');
  for (const folder of [kb, first, second]) fs.mkdirSync(folder);
  fs.writeFileSync(path.join(first, 'first-marker.txt'), 'first');
  fs.writeFileSync(path.join(second, 'second-marker.txt'), 'second');
  const store = require('../src/main/store'), projects = require('../src/main/projects');
  projects.write(kb, 'a', { name: 'プロジェクトA', repos: [{ url: 'git@h:t/first.git', role: 'main' }, { url: 'git@h:t/second.git', role: 'work' }] });
  projects.write(kb, 'b', { name: 'プロジェクトB', repos: [{ url: 'git@h:t/first.git', role: 'main' }] });
  const items = projects.list([kb]);
  const a = items.find(i => i.folder === 'a').key, b = items.find(i => i.folder === 'b').key;
  store.saveConfig(data, { repos: [kb, first, second], knowledgeRepos: [kb], lastRepo: first, lastProject: a,
    repoPaths: { [projects.normalizeUrl('git@h:t/first.git')]: first, [projects.normalizeUrl('git@h:t/second.git')]: second },
    area: 'home', useWorktree: false, transport: 'headless', share: { enabled: false } });
  const make = (repo, project, title) => {
    const session = store.createSession(data, { repo, project, cli: 'codex', transport: 'headless', title });
    store.appendMessage(data, session.id, { role: 'user', text: title });
    return session;
  };
  make(first, a, 'Aの主リポジトリ');
  const other = make(second, a, 'Aの別リポジトリ');
  store.appendMessage(data, other.id, { role: 'assistant', text: '引き継ぐ回答', complete: true });
  make(first, b, 'Bのセッション');
  let app;
  const launch = async () => {
    app = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', `--user-data-dir=${data}`] });
    const win = await app.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('area-projects')?.onclick === 'function');
    return win;
  };
  try {
    let win = await launch();
    await win.locator('#area-projects').click();
    await win.waitForFunction(() => state.area === 'projects' && document.querySelector('#sessions').textContent.includes('Aの別リポジトリ'));
    assert.equal(await win.locator('#area-workflows + #area-projects').count(), 1);
    assert.equal(await win.locator('#repo-knowledge').count(), 0);
    assert.equal(await win.locator('#changes-toggle').isVisible(), false);
    await win.getByRole('button', { name: 'プロジェクトを編集', exact: true }).click();
    await win.locator('#project-dialog').waitFor({ state: 'visible' });
    await win.locator('#project-repos select').nth(1).selectOption('reference');
    await win.locator('#project-save').click();
    await win.locator('#project-dialog').waitFor({ state: 'hidden' });
    assert.equal(projects.read(a).project.repos[1].role, 'reference');
    assert.equal(await win.locator('#sidebar-repository-slot #repo-select').isVisible(), true);
    assert.equal(await win.locator('#repository-context select').count(), 1);
    assert.equal(await win.locator('#repository-context details').count(), 1);
    assert.deepEqual(await win.locator('#repo-select optgroup').evaluateAll(nodes => nodes.map(n => n.label)), ['プロジェクト']);
    const selector = await win.locator('#repo-select').boundingBox(), sessions = await win.locator('#sessions').boundingBox();
    assert.ok(sessions.y > selector.y + selector.height);
    assert.equal(await win.locator('#sessions .list-pick').count(), 2);
    await win.locator('#sessions .list-pick').filter({ hasText: 'Aの別リポジトリ' }).click();
    await win.waitForFunction(id => state.current?.id === id, other.id);
    assert.equal(await win.evaluate(() => state.area), 'projects');
    assert.equal(await win.evaluate(() => state.repo), second);
    assert.equal(await win.locator('#changes-toggle').isVisible(), true);
    await win.locator('#view-files').click();
    assert.equal(await win.locator('#project-file-repo option').count(), 3);
    await win.locator('#project-file-repo').selectOption(first);
    await win.locator('#tree .name').filter({ hasText: 'first-marker.txt' }).waitFor();
    assert.equal(await win.evaluate(() => state.repo), second, 'file browsing does not change execution repo');
    assert.equal(await win.evaluate(() => state.current.id), other.id, 'file browsing preserves session');
    await win.locator('#project-file-repo').selectOption(second);
    await win.locator('#tree .name').filter({ hasText: 'second-marker.txt' }).waitFor();
    await win.evaluate(() => renderAreaContext());
    assert.equal(await win.locator('#files').isVisible(), true, 'sidebar refresh preserves the selected view');
    await win.locator('#view-chat').click();
    await win.locator('#repo-select').selectOption(`project:${b}`);
    await win.waitForFunction(() => document.querySelector('#sessions').textContent.includes('Bのセッション') && state.current === null);
    assert.equal(await win.locator('#sessions .list-pick').count(), 1);
    await win.locator('#area-work').click();
    await win.locator('#repo-select').selectOption(kb);
    await win.locator('#area-projects').click();
    await win.waitForFunction(() => !state.config.lastProject);
    assert.equal(await win.locator('#changes-toggle').isVisible(), false);
    assert.equal(await win.locator('#sessions .list-pick').count(), 0);
    assert.equal(await win.locator('#send').isDisabled(), true);
    await win.locator('#repo-select').selectOption(`project:${a}`);
    await win.waitForFunction(() => document.querySelector('#sessions .list-pick') && !document.querySelector('#send').disabled);
    await win.screenshot({ path: '/tmp/agent-app-project-navigation.png' });
    await app.close(); app = null;
    win = await launch();
    await win.waitForFunction(() => state.area === 'projects' && document.querySelectorAll('#sessions .list-pick').length === 2);
    assert.equal(await win.locator('#repo-select').inputValue(), `project:${a}`);
    assert.equal(await win.locator('#area-projects').getAttribute('aria-current'), 'page');
    await win.locator('#area-home').click();
    await win.locator('#home-context-slot #repo-select').waitFor({ state: 'visible' });
    assert.equal(await win.locator('[data-home-area]').count(), 4, 'project selection keeps the portal visible');
    await win.locator('#area-work').click();
    assert.equal(await win.locator('#sidebar-repository-slot #repo-select').isVisible(), true);
    for (const area of ['conversation', 'tasks', 'workflows']) {
      await win.evaluate(area => showArea(area), area);
      assert.equal(await win.locator('#sidebar-repository-slot #repo-select').isVisible(), true);
      await win.locator('#repo-select').selectOption(second);
      await win.waitForFunction(root => state.repo === root && !state.config.lastProject, second);
      assert.equal(await win.locator('#repo-select').inputValue(), second);
      await win.locator('#repo-more summary').click();
      assert.equal(await win.locator('#project-edit').isVisible(), false);
      assert.equal(await win.locator('#repo-remove').isVisible(), true);
      await win.locator('#repo-more summary').click();
      await win.locator('#repo-select').selectOption(`project:${a}`);
      await win.waitForFunction(key => state.config.lastProject === key && !state.current, a);
      assert.equal(await win.evaluate(() => state.repo), first);
      await win.locator('#repo-more summary').click();
      assert.equal(await win.locator('#project-edit').isVisible(), true);
      assert.equal(await win.locator('#repo-remove').isVisible(), false);
      await win.locator('#repo-more summary').click();
    }
    await win.evaluate(async ({ repo, id }) => { await showArea('conversation'); await openSessionInRepo(repo, id); }, { repo: second, id: other.id });
    await win.locator('#conversation-repository summary').click();
    await win.locator('#conversation-repo').selectOption(first);
    await win.locator('#search-transfer-dialog').waitFor({ state: 'visible' });
    assert.equal(await win.locator('#search-target-repo').inputValue(), first, 'fork dialog receives selected destination');
    assert.equal(await win.evaluate(() => state.current.id), other.id, 'source session is preserved');
    await win.evaluate(() => document.getElementById('search-transfer-dialog').close());
    await win.locator('#area-inbox').click();
    assert.equal(await win.locator('#sidebar-repository-slot').isVisible(), false);
    await win.evaluate(({ first, second }) => {
      state.attention = { unread: 2, action: 0, items: [first, second].map((repo, i) => ({ key: String(i), repo, kind: 'conversation', queue: 'unread', title: `通知${i}`, target: {} })) };
      renderInboxItems();
    }, { first, second });
    assert.equal(await win.locator('#inbox-items .list-pick').count(), 2);
    await win.screenshot({ path: '/tmp/agent-app-unified-context.png' });
  } finally {
    if (app) await app.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
