'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const APP = path.resolve(__dirname, '..');

function playwright() {
  try { return require('playwright'); } catch {}
  try { return require(path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')); } catch { return null; }
}

test('workflow editor shows the embedded tmux screen and composer for existing and new workflows', { timeout: 40000 }, async (t) => {
  const pw = playwright();
  let electronBinary;
  try { electronBinary = require('electron'); } catch { /* Electron is optional in source-only test runs. */ }
  if (!pw?._electron || !electronBinary || !fs.existsSync(electronBinary)) return t.skip('Electron and Playwright are required');
  if (process.platform === 'linux' && !process.env.DISPLAY) return t.skip('A display is required for Electron');

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'workflow-teaching-ui-'));
  const repo = path.join(temp, 'repo');
  const userData = path.join(temp, 'userdata');
  const bin = path.join(temp, 'bin');
  fs.mkdirSync(repo);
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'agent-herd'), [
    '#!/bin/sh',
    'case "$1" in',
    '  route) echo \'{"handling":{"choice":"flow","confidence":0.95},"task":null,"flow":{"choice":"review-flow","confidence":0.94},"skills":[],"routine":null,"hold":true,"stage":"judge","abstained":[]}\';;',
    'esac',
    'exit 0',
    '',
  ].join('\n'), { mode: 0o755 });
  require('../src/main/store').saveConfig(userData, {
    repos: [repo], lastRepo: repo, area: 'home', transport: 'headless', share: { enabled: false }, evaluation: { mode: 'off' },
    execution: { defaultPolicy: 'direct', tiers: { small: { cli: 'codex', model: '' }, medium: { cli: 'codex', model: '' }, large: { cli: 'codex', model: '' } } },
  });
  require('../src/main/automation/flow-store').save(repo, {
    version: 2, id: 'review-flow', name: '既存のレビュー', description: '変更を確認する',
    purpose: 'implementation', entry: ['review'], exit: ['review'],
    nodes: [{ id: 'review', label: '変更を確認', kind: 'work', goal: '{{request}} を確認する', deps: [], tier: 'auto' }],
  }, 'create');

  let electron;
  try {
    electron = await pw._electron.launch({ executablePath: electronBinary, args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
      env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH || ''}` } });
    const win = await electron.firstWindow();
    await win.waitForFunction(() => typeof document.getElementById('area-workflows')?.onclick === 'function');
    await electron.evaluate(({ ipcMain }) => {
      const read = ipcMain._invokeHandlers.get('automation:flow:teach:session');
      const replace = (channel, handler) => { ipcMain.removeHandler(channel); ipcMain.handle(channel, handler); };
      global.flowStartCalls = [];
      global.flowWatchCalls = [];
      global.releaseFlowStart = null;
      global.releaseAutoStart = null;
      replace('agents:list', () => ({ ok: true, data: [{ name: 'codex', available: true, interactive: true }] }));
      replace('term:open', () => ({ ok: true, data: { phase: 'ready', name: 'workflow-test-terminal' } }));
      replace('term:watch', (event, { id }) => {
        global.flowWatchCalls.push(id);
        const text = `WORKFLOW TERMINAL READY ${global.flowWatchCalls.length}`;
        setTimeout(() => event.sender.send('term:screen', { id, text, cursor: { x: 0, y: 0 }, cols: 80, rows: 24 }), 20);
        return { ok: true, data: true };
      });
      replace('term:resize', () => ({ ok: true, data: true }));
      replace('automation:flow:teach:start', async (event, payload) => {
        const view = await read(event, payload);
        global.flowStartCalls.push({ workflowId: payload.workflowId, sessionId: view.data.session.id });
        if (global.flowStartCalls.length === 1) await new Promise(resolve => { global.releaseFlowStart = resolve; });
        if (global.flowStartCalls.length === 2) {
          event.sender.send('turn:transport', { id: view.data.session.id, transport: 'tmux' });
          await new Promise(resolve => { global.releaseAutoStart = resolve; });
        }
        return { ok: true, data: { ...view.data, started: true } };
      });
    });
    await win.reload();
    await win.waitForFunction(() => typeof document.getElementById('area-home')?.onclick === 'function');
    await win.setViewportSize({ width: 1360, height: 900 });

    // Home routes the request to the saved workflow; its steps then enter the editor.
    await win.locator('#prompt').fill('既存のレビューで変更を確認して');
    await win.locator('#send').click();
    await win.waitForFunction(() => state.area === 'workflows' && state.selectedWorkflow === 'review-flow');
    assert.equal(await win.locator('#automation').isVisible(), true);
    assert.equal(require('../src/main/store').listSessions(userData, repo).length, 0, 'routing does not launch a conversation');
    // A saved workflow follows the public path: 手順 → 編集 → 編集開始.
    const workbench = win.locator('#automation-workbench');
    await workbench.locator('[data-flow-tab="steps"]').click();
    await workbench.locator('[data-flow-edit]').click();
    await win.locator('#flow-teach-start').waitFor();
    await workbench.locator('.flow-teaching-candidate').waitFor();
    await workbench.locator('.flow-teaching-trial').waitFor();
    assert.match(await workbench.locator('.flow-teaching-candidate').textContent(), /候補の工程/);
    assert.match(await workbench.locator('.flow-teaching-trial').textContent(), /テスト実行/);
    assert.equal(await win.locator('#flow-teach-terminal').isVisible(), false);
    await win.locator('#flow-teach-launch .teach-execution-settings > summary').click();
    await win.locator('#flow-teach-agent').selectOption('codex');
    await win.locator('#flow-teach-start').click();

    await win.waitForFunction(() => document.querySelector('#flow-teach-term-host .xterm-rows')?.textContent.includes('WORKFLOW TERMINAL READY 1'));
    const firstSession = await win.evaluate(() => FlowTerm.current());
    assert.ok(firstSession, 'the embedded terminal is attached to the workflow session');
    assert.equal(await win.locator('#flow-teach-terminal').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-composer').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-mode-message').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-prompt').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-composer-toolbar').isVisible(), true);
    await win.screenshot({ path: '/private/tmp/agent-app-workflow-teaching-editor.png' });
    assert.equal(await win.locator('#flow-teach-launch').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-create').isVisible(), false);
    assert.equal(await win.locator('#flow-teach-placeholder').isVisible(), false);
    const layout = await win.evaluate(() => {
      const shadow = document.getElementById('automation-workbench').shadowRoot;
      const measure = (element) => {
        if (!element) return null;
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return { top: box.top, bottom: box.bottom, height: box.height, left: box.left, right: box.right,
          overflowY: style.overflowY, gridTemplateRows: style.gridTemplateRows, display: style.display };
      };
      const light = (id) => measure(document.getElementById(id));
      const inside = (selector) => measure(shadow.querySelector(selector));
      return { viewport: { width: innerWidth, height: innerHeight }, terminal: light('flow-teach-terminal'),
        composer: light('flow-teach-composer'), send: light('flow-teach-send'),
        teaching: light('flow-teaching'), slot: inside('slot[name="flow-teaching"]'),
        conversationEditor: inside('.task-conversation-editor'), tabPanel: inside('.task-tab-panel'),
        detailShell: inside('.flow-detail-shell'), executionDetail: inside('.execution-detail'),
        machinePane: inside('.machine-pane'), flowScreen: inside('.flow-screen.is-teaching'),
        flowLayout: inside('.flow-layout') };
    });
    if (process.env.AGENT_APP_WORKFLOW_GEOMETRY) console.log('workflow editor layout:', JSON.stringify(layout));
    assert.ok(layout.terminal.height >= 220, JSON.stringify(layout));
    assert.ok(layout.terminal.bottom <= layout.composer.top, JSON.stringify(layout));
    assert.ok(layout.composer.top >= 0 && layout.composer.bottom <= layout.viewport.height + 1, JSON.stringify(layout));
    assert.ok(layout.send.bottom <= layout.viewport.height + 1, JSON.stringify(layout));
    assert.equal(await electron.evaluate(() => flowStartCalls.length), 1);
    assert.equal(await win.evaluate(() => FlowTeaching.state.pending), true, 'the terminal is visible before start completes');

    await electron.evaluate(() => { releaseFlowStart(); releaseFlowStart = null; });
    await win.waitForFunction(() => !FlowTeaching.state.pending);
    assert.equal(await win.evaluate(() => FlowTerm.current()), firstSession);
    assert.match(await win.locator('#flow-teach-term-host .xterm-rows').textContent(), /WORKFLOW TERMINAL READY 1/);
    await workbench.locator('.flow-teaching-candidate details > summary').click();
    assert.match(await workbench.locator('.flow-teaching-candidate .flow-node-summary').textContent(), /変更を確認/);
    const trial = workbench.locator('.flow-teaching-trial');
    await trial.locator('[data-flow-teaching-trial-request]').fill('代表的な変更を確認する');
    assert.equal(await trial.locator('[data-flow-teaching-trial-request]').inputValue(), '代表的な変更を確認する');
    await trial.locator('.run-settings > summary').click();
    assert.equal(await trial.locator('[data-flow-agent]').isVisible(), true);
    assert.equal(await trial.locator('[data-flow-model]').isVisible(), true);
    assert.equal(await trial.locator('[data-flow-readonly]').isVisible(), true);
    assert.equal(await trial.locator('[data-flow-teaching-trial]').isVisible(), true);
    assert.equal(await trial.locator('[data-flow-teaching-trial]').isEnabled(), true);
    await trial.locator('[data-flow-model]').fill('mock-run-model');
    await trial.locator('.run-settings > summary').click();
    await trial.scrollIntoViewIfNeeded();
    await win.screenshot({ path: '/private/tmp/agent-app-workflow-teaching-trial-settings.png' });
    await electron.evaluate(({ ipcMain }) => {
      const replace = (channel, handler) => { ipcMain.removeHandler(channel); ipcMain.handle(channel, handler); };
      global.flowTrialStarts = [];
      const run = {
        runId: 'mock-trial-run', workflowId: 'review-flow', title: 'レビューのテスト実行',
        createdAt: '2026-09-23T00:00:00.000Z', readonly: true, request: '代表的な変更を確認する',
        state: 'done', terminal: true, revision: 1,
        progress: { done: 1, failed: 0, total: 1 }, interactions: [],
        nodes: [{ id: 'review', state: 'done', goal: '代表的な変更を確認する', output: '確認済み' }],
      };
      replace('automation:flow:run:start', (_event, payload) => {
        global.flowTrialStarts.push(payload);
        return { ok: true, data: { runId: run.runId } };
      });
      replace('automation:flow:run:list', () => ({ ok: true, data: [run] }));
      replace('automation:flow:run:read', () => ({ ok: true, data: run }));
      replace('automation:flow:run:log', () => ({ ok: true, data: { tail: 'MOCK FLOW RUN LOG: review complete' } }));
    });
    await trial.locator('[data-flow-teaching-trial]').click();
    await workbench.locator('.flow-log').filter({ hasText: 'MOCK FLOW RUN LOG: review complete' }).waitFor();
    assert.match(await workbench.locator('.execution-title').textContent(), /テスト実行|レビューのテスト実行/);
    assert.equal(await workbench.locator('.flow-log').isVisible(), true);
    assert.deepEqual(await electron.evaluate(() => flowTrialStarts.map(({ source, request, model }) => ({ type: source.type, request, model }))),
      [{ type: 'draft', request: '代表的な変更を確認する', model: 'mock-run-model' }]);
    await workbench.locator('[data-flow-log]').click();
    await win.screenshot({ path: '/private/tmp/agent-app-workflow-teaching-trial.png' });

    // The creation route should end in the same editor, with a different terminal session.
    await win.click('#area-home');
    await win.click('#area-workflows');
    await win.locator('#flow-teach-create').waitFor({ state: 'visible' });
    await win.locator('#flow-teach-save-name').fill('new-review-flow');
    await win.locator('#flow-teach-purpose').fill('新しい変更をレビューする');
    await win.locator('#flow-teach-launch .teach-execution-settings > summary').click();
    await win.locator('#flow-teach-agent').selectOption('auto');
    await win.locator('#flow-teach-start').click();
    await win.waitForFunction(() => document.querySelector('#flow-teach-term-host .xterm-rows')?.textContent.includes('WORKFLOW TERMINAL READY 2'));
    assert.equal(await win.evaluate(() => FlowTeaching.state.pending), true, 'auto selection attaches on the transport event before start completes');
    assert.notEqual(await win.evaluate(() => FlowTerm.current()), firstSession);
    assert.equal(await win.locator('#flow-teach-terminal').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-composer').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-prompt').isVisible(), true);
    assert.equal(await win.locator('#flow-teach-composer-toolbar').isVisible(), true);
    const creationLayout = await win.evaluate(() => {
      const bounds = (id) => {
        const box = document.getElementById(id).getBoundingClientRect();
        return { top: box.top, bottom: box.bottom, height: box.height };
      };
      return { viewportHeight: innerHeight, terminal: bounds('flow-teach-terminal'),
        composer: bounds('flow-teach-composer'), send: bounds('flow-teach-send') };
    });
    if (process.env.AGENT_APP_WORKFLOW_GEOMETRY) console.log('workflow creation layout:', JSON.stringify(creationLayout));
    await win.screenshot({ path: '/private/tmp/agent-app-workflow-teaching-create.png' });
    assert.ok(creationLayout.terminal.height >= 220, JSON.stringify(creationLayout));
    assert.ok(creationLayout.composer.bottom <= creationLayout.viewportHeight + 1, JSON.stringify(creationLayout));
    assert.ok(creationLayout.send.bottom <= creationLayout.viewportHeight + 1, JSON.stringify(creationLayout));
    assert.deepEqual(await electron.evaluate(() => flowStartCalls.map(call => call.workflowId)), ['review-flow', 'new-review-flow']);
    await electron.evaluate(() => { releaseAutoStart(); releaseAutoStart = null; });
    await win.waitForFunction(() => !FlowTeaching.state.pending);
  } finally {
    if (electron) {
      await electron.evaluate(() => { if (global.releaseFlowStart) global.releaseFlowStart(); }).catch(() => {});
      await electron.evaluate(() => { if (global.releaseAutoStart) global.releaseAutoStart(); }).catch(() => {});
      await electron.close();
    }
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
