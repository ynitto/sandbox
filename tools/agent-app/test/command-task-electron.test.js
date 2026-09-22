'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const APP = path.join(__dirname, '..');
function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}
test('command task: create, retain selection, edit and run without AI', async (t) => {
  const pw = playwright();
  if (!pw?._electron || process.platform !== 'darwin') return t.skip('local Electron test requires Playwright and macOS Python');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'command-task-ui-'));
  const repo = path.join(temp, 'repo'), userData = path.join(temp, 'userdata'), bin = path.join(temp, 'bin');
  fs.mkdirSync(path.join(repo, '.agents'), { recursive: true });
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(repo, '.agents/agent-loop.yml'), 'prompts: []\n');
  const quote = (v) => "'" + v.replaceAll("'", "'\\''") + "'";
  fs.writeFileSync(path.join(bin, 'agent-loop'), '#!/bin/sh\nexec /usr/bin/python3 ' + quote(process.env.AGENT_APP_TEST_LOOP || path.resolve(APP, '../agent-loop/agent-loop.py')) + ' "$@"\n', { mode: 0o755 });
  require('../src/main/store').saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'work' });
  require('../src/main/automation/store').save(repo, { name: 'Existing task', machine: 'existing', purpose: 'test', steps: [{ kind: 'agent', title: 'test', detail: 'test' }] });
  const electron = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
    env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}`, AGENT_LOOP_RUN_DIR: path.join(temp, 'runs'), AGENT_LOOP_RUN_HISTORY_DIR: path.join(temp, 'history') } });
  try {
    const win = await electron.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('area-tasks')?.onclick === 'function');
    await win.click('#area-tasks');
    const panel = win.locator('#automation-workbench');
    await win.locator('#task-create').waitFor();
    await win.screenshot({ path: '/tmp/agent-app-ux-task-create.png' });
    assert.equal(await win.locator('#areas > button').first().getAttribute('id'), 'area-home');
    assert.equal(await panel.locator('#command-add').count(), 0);
    await win.click('#task-create-manual');
    await panel.locator('.manual-task-create').waitFor();
    assert.equal(await panel.locator('#manual-steps').isVisible(), true);
    assert.equal(await panel.locator('.task-detail-tabs').count(), 0);
    await panel.locator('#schedule-kind').selectOption('weekly');
    assert.equal(await panel.locator('.manual-task-create').isVisible(), true);
    await win.screenshot({ path: '/tmp/agent-app-ux-manual.png' });
    await panel.locator('#schedule-name').fill('My command');
    await panel.locator('#schedule-command').fill('echo command-first');
    await panel.locator('#schedule-enabled').uncheck();
    await panel.locator('#schedule-save').click();
    await win.locator('#tasks .row-item.active').filter({ hasText: 'My command' }).waitFor();
    assert.match(await panel.locator('.execution-title').textContent(), /My command/);
    await panel.locator('#command-edit').click();
    await panel.locator('#schedule-name').fill('Renamed command');
    await panel.locator('#schedule-command').fill('echo command-updated\necho command-second');
    await panel.locator('#schedule-save').click();
    await win.locator('#tasks .row-item.active').filter({ hasText: 'Renamed command' }).waitFor();
    assert.match(await panel.locator('.execution-title').textContent(), /Renamed command/);
    await panel.locator('#run-start').click();
    await panel.locator('.run-result.ok').waitFor();
    assert.match(await panel.locator('#run-log').textContent(), /command-updated[\s\S]*command-second/);
    await win.locator('#tasks .row-item').filter({ hasText: 'Existing task' }).click();
    await panel.locator('.execution-title').filter({ hasText: 'Existing task' }).waitFor();
    assert.equal(await panel.locator('.run-result.ok').count(), 0);
    assert.doesNotMatch(await panel.locator('#run-log').textContent(), /command-updated/);
    await win.locator('#tasks .row-item').filter({ hasText: 'Renamed command' }).click();
    await panel.locator('.run-result.ok').waitFor();
    assert.match(await panel.locator('#run-log').textContent(), /command-updated/);
    const badge = await panel.locator('.schedule-list .status').boundingBox();
    assert.ok(badge.width < 100, `status should be compact: ${badge.width}`);
    // アプリ経由の run:exit が来ない定期実行結果を、実際の履歴ストアへ追記する。
    const scheduledLog = path.join(temp, 'runs', 'scheduled.jsonl');
    fs.mkdirSync(path.dirname(scheduledLog), { recursive: true });
    fs.writeFileSync(scheduledLog, 'scheduled command output\n');
    const recordScheduled = (runId) => {
      const result = require('node:child_process').spawnSync('/usr/bin/python3', ['-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import agent_loop as al; al.record_repository_run(sys.argv[2], {"runId": sys.argv[3], "kind": "command", "entryName": "Renamed command", "source": "scheduled", "ok": True, "finishedAt": "2026-09-13T10:00:00Z", "logFile": sys.argv[4]})',
        path.resolve(APP, '../agent-loop'), repo, runId, scheduledLog],
        { env: { ...process.env, AGENT_LOOP_RUN_HISTORY_DIR: path.join(temp, 'history') }, encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
    };
    recordScheduled('scheduled-before-tab');
    await panel.locator('[data-task-tab="history"]').click();
    const scheduledRows = panel.locator('.run-history li').filter({ hasText: '定期実行' });
    await scheduledRows.first().waitFor();
    assert.equal(await scheduledRows.count(), 1);
    await scheduledRows.first().locator('[data-history-log]').click();
    await panel.locator('.history-log pre').filter({ hasText: 'scheduled command output' }).waitFor();
    recordScheduled('scheduled-while-open');
    await scheduledRows.nth(1).waitFor({ timeout: 20000 });
    const loopPath = path.join(bin, 'agent-loop');
    const workingLoop = fs.readFileSync(loopPath, 'utf8');
    fs.writeFileSync(loopPath, '#!/bin/sh\necho "test inspect failure" >&2\nexit 1\n');
    await panel.locator('[data-task-tab="overview"]').click();
    await panel.locator('[data-task-tab="history"]').click();
    const connectionNote = panel.locator('details').filter({ hasText: 'test inspect failure' });
    await connectionNote.locator('summary').waitFor();
    assert.equal(await connectionNote.getAttribute('open'), null);
    await connectionNote.locator('summary').click();
    assert.equal(await panel.locator('[data-task-tab="history"]').isEnabled(), true);
    assert.match(await panel.locator('.execution-title').textContent(), /Renamed command/);
    assert.equal(await scheduledRows.count(), 2);
    fs.writeFileSync(loopPath, workingLoop);
    await panel.locator('#snapshot-retry').click();
    await panel.locator('#snapshot-retry').waitFor({ state: 'detached' });
    await win.click('#area-workflows');
    await win.locator('#flow-teach-create').waitFor();
    await win.screenshot({ path: '/tmp/agent-app-ux-workflow-create.png' });
    assert.equal(await win.locator('#flow-teach-manual').isVisible(), true);
    await win.click('#flow-teach-manual');
    await panel.locator('[data-flow-save]').waitFor();
    await win.click('#area-tasks');
    await win.locator('#task-create').waitFor();
    assert.equal(await win.locator('#task-create .flow-patterns').count(), 0);
    const taskLayout = await Promise.all(['#task-machine', '#task-purpose', '#task-create .task-actions'].map(id => win.locator(id).boundingBox()));
    await win.click('#area-workflows');
    await win.locator('#flow-teach-create').waitFor();
    assert.equal(await win.locator('#flow-teach-launch .flow-patterns').count(), 0);
    const flowLayout = await Promise.all(['#flow-teach-save-name', '#flow-teach-purpose', '#flow-teach-launch .task-actions'].map(id => win.locator(id).boundingBox()));
    for (let i = 0; i < taskLayout.length; i++) {
      for (const key of ['x', 'y', 'width', 'height']) assert.ok(Math.abs(taskLayout[i][key] - flowLayout[i][key]) < 2, `creation layout ${i} ${key}`);
    }
    await win.click('#flow-teach-manual');
    await panel.locator('[data-flow-save]').waitFor();
    const catalog = await win.evaluate(() => api.automation.flowCatalog());
    assert.ok(catalog.patterns.length > 0);
    await panel.locator('.flow-patterns > summary').click();
    const patterns = win.locator('[data-flow-pattern]');
    await patterns.first().waitFor();
    assert.equal(await patterns.count(), catalog.patterns.length);
    await win.screenshot({ path: '/tmp/agent-app-ux-workflow-patterns.png' });
    await patterns.first().click();
    await panel.locator('[data-flow-save]').waitFor();
    assert.equal(await win.locator('#area-workflows').getAttribute('aria-current'), 'page');
    assert.equal(await panel.locator('select[data-flow-pattern]').count(), 0);
    assert.equal(await panel.locator('[data-flow-meta="name"]').inputValue(), catalog.patterns[0].template.name || catalog.patterns[0].label);
    assert.equal(await panel.locator('[data-flow-node][data-key="id"]').count(), catalog.patterns[0].template.nodes.length);
    await panel.locator('[data-flow-save]').click();
    await win.locator('#workflows .list-pick').filter({ hasText: catalog.patterns[0].template.name || catalog.patterns[0].label }).waitFor();
  } finally { await electron.close(); }
});

test('startup restores the workflow creation form before any navigation', async (t) => {
  const pw = playwright();
  if (!pw?._electron || process.platform !== 'darwin') return t.skip('local Electron test requires Playwright and macOS');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'workflow-startup-ui-'));
  const repo = path.join(temp, 'repo'), userData = path.join(temp, 'userdata');
  fs.mkdirSync(repo);
  require('../src/main/store').saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'workflows' });
  const electron = await pw._electron.launch({ executablePath: require('electron'), args: [APP, '--no-sandbox', `--user-data-dir=${userData}`] });
  try {
    const win = await electron.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('session-new')?.onclick === 'function');
    const status = await win.evaluate(() => ({
      title: document.getElementById('automation-workbench').shadowRoot.querySelector('.teaching-create h2')?.textContent,
      visible: window.FlowTeaching.state.visible,
      creating: window.FlowTeaching.state.creating,
      hidden: document.getElementById('flow-teaching').hidden,
    }));
    assert.equal(status.title, '新しいワークフロー');
    assert.deepEqual(status, { title: '新しいワークフロー', visible: true, creating: true, hidden: false });
    await win.locator('#flow-teach-purpose').waitFor();
    await win.locator('#flow-teach-save-name').fill('startup-workflow');
    await win.click('#flow-teach-manual');
    await win.locator('#automation-workbench [data-flow-save]').waitFor();
  } finally { await electron.close(); fs.rmSync(temp, { recursive: true, force: true }); }
});
