'use strict';

// Electron を実際に起動し、親画面→タスク／ワークフロー iframe→IPC の境界を通す。
// DOM 単体では contextBridge やフレーム間の API 参照切れを検出できないため、
// 登録済みリポジトリのワークフローが表示されるところまで確認する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = path.join(__dirname, '..');

function electronBinary() {
  try {
    const binary = require('electron');
    return typeof binary === 'string' && fs.existsSync(binary) ? binary : '';
  } catch { return ''; }
}

function playwright() {
  for (const id of ['playwright', 'playwright-core']) {
    try { return require(id); } catch { /* 次を試す */ }
  }
  const nodePrefix = path.dirname(path.dirname(process.execPath));
  try { return require(path.join(nodePrefix, 'lib', 'node_modules', '@playwright', 'cli', 'node_modules', 'playwright-core')); } catch { /* 次を試す */ }
  try { return require('/opt/node22/lib/node_modules/playwright'); } catch { return null; }
}

test('実機: 会話・タスク・ワークフローを移動し、登録済み項目を開ける', async (t) => {
  const binary = electronBinary();
  const pw = playwright();
  if (!binary) { t.skip('electron のバイナリが無い'); return; }
  if (!pw || !pw._electron) { t.skip('Playwright の Electron ドライバが無い'); return; }
  if (process.platform === 'linux' && !process.env.DISPLAY) { t.skip('表示先が無い'); return; }

  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-repo-'));
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-automation-userdata-'));
  const appStore = require('../src/main/store');
  const machineStore = require('statemachine-maker/src/main/store');
  const flowStore = require('statemachine-maker/src/main/flow-store');
  machineStore.save(repo, {
    name: 'リリース確認', machine: 'release-check', purpose: '自動化統合の確認',
    steps: [{ kind: 'agent', title: '変更を確認', detail: '公開前の変更を確認する' }],
  });
  flowStore.save(repo, {
    version: 2, id: 'parallel-review', name: '並列レビュー', description: '複数の観点で変更を確認する',
    purpose: 'implementation', entry: ['review'], exit: ['review'],
    nodes: [{ id: 'review', label: '変更を確認', kind: 'work', goal: '{{request}} を確認する', deps: [], tier: 'auto' }],
  }, 'create');
  appStore.saveConfig(userData, { repos: [repo], lastRepo: repo, area: 'work' });
  const session = appStore.createSession(userData, {
    repo, cli: 'codex', model: 'gpt-test', policy: 'quality', tier: 'large', transport: 'headless',
  });
  appStore.appendMessage(userData, session.id, { role: 'user', text: '画面を確認して' });
  appStore.appendMessage(userData, session.id, {
    role: 'assistant', cli: 'codex', model: 'gpt-test', text: '確認できました。', elapsedMs: 1200,
    parts: {
      thinking: [{ text: '関連する画面を確認した', status: 'done' }],
      information: [{ type: 'file', title: 'src/renderer.js', action: 'modified', status: 'success' }],
    },
  });

  const electron = await pw._electron.launch({
    executablePath: binary,
    args: [APP, '--no-sandbox', `--user-data-dir=${userData}`],
  });
  const errors = [];
  try {
    const win = await electron.firstWindow();
    win.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
    win.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

    await win.waitForSelector('#area-tasks');
    await win.waitForFunction(() => typeof document.getElementById('area-tasks').onclick === 'function', null, { timeout: 20000 });
    assert.match(await win.textContent('#side'), /会話.*タスク.*ワークフロー/s);
    await win.click('#sessions .list-pick');
    await win.locator('.answer-bubble').waitFor();
    assert.match(await win.locator('.msg.user').textContent(), /画面を確認して/);
    assert.match(await win.locator('.answer-bubble').textContent(), /確認できました/);
    assert.strictEqual(await win.locator('.response-disclosure.thinking').getAttribute('open'), null, '完了後の思考は閉じる');
    assert.strictEqual(await win.locator('.response-disclosure.information').getAttribute('open'), null, '成功時の実行情報は閉じる');
    if (process.env.AGENT_APP_CHAT_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_CHAT_SCREENSHOT });

    await win.click('#settings-open');
    await win.locator('#app-settings[open]').waitFor();
    await win.click('[data-settings-tab="instructions"]');
    await win.fill('#instruction-text', '回答は簡潔な日本語にする');
    await win.fill('#skill-entry', 'self-checking');
    await win.click('#skill-add');
    await win.click('#startup-add');
    await win.fill('.startup-row input', 'brainstorming');
    await win.click('[data-settings-tab="execution"]');
    await win.check('input[name="default-policy"][value="quality"]');
    await win.fill('#tier-large-model', 'gpt-quality');
    if (process.env.AGENT_APP_SETTINGS_SCREENSHOT) await win.screenshot({ path: process.env.AGENT_APP_SETTINGS_SCREENSHOT });
    await win.click('#settings-save');
    await win.waitForFunction(() => document.getElementById('settings-status').textContent === '保存しました');
    const saved = appStore.loadConfig(userData);
    assert.strictEqual(saved.instructions.text, '回答は簡潔な日本語にする');
    assert.deepStrictEqual(saved.instructions.skills, ['self-checking']);
    assert.deepStrictEqual(saved.instructions.startupActions, [{ type: 'skill', value: 'brainstorming', onError: 'warn' }]);
    assert.strictEqual(saved.execution.defaultPolicy, 'quality');
    assert.strictEqual(saved.execution.tiers.large.model, 'gpt-quality');
    await win.click('#settings-close');

    await win.click('#area-tasks');
    await win.waitForFunction(() => document.getElementById('automation-frame').getAttribute('src') !== 'about:blank');

    const workspace = win.frameLocator('#automation-frame');
    await win.locator('#tasks li').first().waitFor({ timeout: 20000 });
    assert.strictEqual(await win.locator('#tasks .list-pick').count(), 1, `タスク一覧を取得できない: ${await win.locator('#tasks').textContent()} / ${errors.join(' | ')}`);
    assert.match(await win.locator('#tasks').textContent(), /リリース確認/);
    await workspace.locator('.task-detail-tabs').waitFor({ timeout: 20000 });
    assert.match(await workspace.locator('.execution-title').textContent(), /タスク.*リリース確認/s);
    assert.strictEqual(await workspace.locator('.folder-pane').isHidden(), true, 'リポジトリ一覧が二重に表示されている');
    assert.strictEqual(await workspace.locator('.home-tabs').isHidden(), true, '主要タブが二重に表示されている');
    if (process.env.AGENT_APP_TASK_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_TASK_SCREENSHOT });
    }
    await workspace.locator('[data-task-tab="steps"]').click();
    await workspace.locator('[data-step="0"]').waitFor();
    assert.match(await workspace.locator('[data-step="0"]').textContent(), /変更を確認/);
    await workspace.locator('#btn-home').click();

    await win.click('#area-workflows');
    await win.locator('#workflows .list-pick').first().waitFor({ timeout: 20000 });
    assert.match(await win.locator('#workflows').textContent(), /並列レビュー/);
    await workspace.locator('.flow-overview').waitFor({ timeout: 20000 });
    assert.match(await workspace.locator('.flow-overview').textContent(), /変更を確認/);
    await workspace.locator('[data-flow-edit]').click();
    await workspace.locator('.flow-node-card').waitFor();
    assert.strictEqual(await workspace.locator('.flow-node-card').count(), 1, 'ワークフローの工程を編集できない');
    assert.strictEqual(await workspace.locator('[data-flow-start]').count(), 0, '編集時に実行フォームを重ねて出さない');
    await workspace.locator('[data-flow-close-editor]').click();
    await win.click('#session-new');
    await workspace.locator('.flow-editor').waitFor();
    assert.match(await workspace.locator('.flow-editor h2').textContent(), /新しく作る/);
    await workspace.locator('[data-flow-close-editor]').click();
    if (process.env.AGENT_APP_FLOW_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_FLOW_SCREENSHOT });
    }
    if (process.env.AGENT_APP_AUTOMATION_SCREENSHOT) {
      await win.screenshot({ path: process.env.AGENT_APP_AUTOMATION_SCREENSHOT });
    }
    await win.click('#area-work');
    assert.strictEqual(await win.locator('#main').isVisible(), true, '会話画面へ戻れない');
    assert.deepStrictEqual(errors, [], '画面でエラーが発生した');
  } finally {
    await electron.close();
  }
});
