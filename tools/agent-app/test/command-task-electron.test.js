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
  } finally { await electron.close(); }
});
