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
    env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}`, AGENT_LOOP_RUN_DIR: path.join(temp, 'runs') } });
  try {
    const win = await electron.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('area-tasks')?.onclick === 'function');
    await win.click('#area-tasks');
    const panel = win.locator('#automation-workbench');
    await panel.locator('.task-detail-tabs').waitFor();
    assert.equal(await panel.locator('#command-add').count(), 0);
    await win.click('#session-new');
    await panel.locator('#command-add:not([disabled])').waitFor();
    await panel.locator('#command-add').click();
    await panel.locator('#schedule-name').fill('My command');
    await panel.locator('#schedule-command').fill('echo command-first');
    await panel.locator('#schedule-enabled').uncheck();
    await panel.locator('#schedule-save').click();
    await win.locator('#tasks .row-item.active').filter({ hasText: 'My command' }).waitFor();
    assert.match(await panel.locator('.execution-title').textContent(), /My command/);
    await panel.locator('#command-edit').click();
    await panel.locator('#schedule-name').fill('Renamed command');
    await panel.locator('#schedule-command').fill('echo command-updated');
    await panel.locator('#schedule-save').click();
    await win.locator('#tasks .row-item.active').filter({ hasText: 'Renamed command' }).waitFor();
    assert.match(await panel.locator('.execution-title').textContent(), /Renamed command/);
    await panel.locator('#run-start').click();
    await panel.locator('.run-result.ok').waitFor();
    assert.match(await panel.locator('#run-log').textContent(), /command-updated/);
    const badge = await panel.locator('.schedule-list .status').boundingBox();
    assert.ok(badge.width < 100, `status should be compact: ${badge.width}`);
  } finally { await electron.close(); }
});
